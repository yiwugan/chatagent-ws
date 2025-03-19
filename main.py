import json
import secrets
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional, List, Annotated

import aiohttp
import time
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.chatagent_ws.AppConfig import APP_CONNECTION_MAX_SESSIONS_PER_IP, \
    APP_CONNECTION_MAX_REQUESTS_PER_MINUTE, APP_SECURITY_TOKEN_EXPIRY_SECONDS, APP_API_HOST, APP_API_PORT, \
    APP_WS_PORT, APP_API_KEY, APP_WS_API_KEY, APP_ENV
from src.chatagent_ws.LoggingUtil import *

load_dotenv()
logger = get_logger("chatagent-ws")

# Configuration
app = FastAPI()
# Enable CORS with credentials support
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Token storage and configuration
session_tokens = {}
token_lock = Lock()

# Rate limiting configuration
session_counts = defaultdict(int)
request_counts = defaultdict(list)
rate_limit_lock = Lock()


class AIResponse(BaseModel):
    message: str
    suggestions: Optional[List[str]] = None
    sources: Optional[List[str]] = None


class ChatHistory:
    def __init__(self):
        self.history = {}
        self.lock = Lock()

    def get_history(self, session_id):
        with self.lock:
            if session_id not in self.history:
                self.history[session_id] = []
            return self.history[session_id]

    def clear_history(self, session_id):
        with self.lock:
            self.history[session_id] = []

    def append(self, session_id, message):
        with self.lock:
            if session_id not in self.history:
                self.history[session_id] = []
            self.history[session_id].append(message)


chat_history = ChatHistory()


def generate_session_token(session_id, client_ip):
    token = secrets.token_urlsafe(32)
    expiry = datetime.now() + timedelta(seconds=APP_SECURITY_TOKEN_EXPIRY_SECONDS)

    with token_lock:
        expired = [t for t, info in session_tokens.items() if info["expiry"] < datetime.now()]
        for t in expired:
            del session_tokens[t]
        session_tokens[token] = {"expiry": expiry, "session_id": session_id, "ip": client_ip}
    return token


# API key validation dependency
async def verify_api_key(x_api_key: Annotated[str, Header()]
                         , request: Request):
    client_ip = request.client.host  # Get client IP address
    if x_api_key != APP_WS_API_KEY:
        logger.warning(f"AUDIT: Invalid APP_API_KEY {x_api_key} from IP {client_ip}")
        raise HTTPException(
            status_code=401,
            detail="Invalid API Key"
        )
    return x_api_key


def validate_token(token, client_ip):
    with token_lock:
        if token not in session_tokens:
            logger.warning(f"AUDIT: Invalid token {token} from IP {client_ip}")
            return False, "Invalid token"
        token_info = session_tokens[token]
        if token_info["ip"] != client_ip:
            logger.warning(f"AUDIT: token {token} IP mismatch from IP {client_ip}")
            return False, "Token IP mismatch"
        if token_info["expiry"] < datetime.now():
            del session_tokens[token]
            logger.info(f"AUDIT: token {token} expired from IP {client_ip}")
            return False, "Token expired"
        return True, token_info["session_id"]


@app.post("/api/get_session_token")
async def get_session_token(request: Request,
                      api_key: str = Depends(verify_api_key)):
    logger.debug(f"get_session_token enter")

    client_ip = request.client.host
    with rate_limit_lock:
        if session_counts[client_ip] >= APP_CONNECTION_MAX_SESSIONS_PER_IP:
            logger.info(f"AUDIT: More than {APP_CONNECTION_MAX_SESSIONS_PER_IP} sessions from this IP {client_ip}, reject request")
            raise HTTPException(status_code=429, detail=f"More than {APP_CONNECTION_MAX_SESSIONS_PER_IP} sessions from this IP {client_ip}, reject request")

    session_id = str(uuid.uuid4())
    token = generate_session_token(session_id, client_ip)
    with rate_limit_lock:
        session_counts[client_ip] += 1

    logger.debug(f"get_session_token exit")
    return {
        "token": token,
        "expires_in": APP_SECURITY_TOKEN_EXPIRY_SECONDS,
        "session_id": session_id
    }


def check_rate_limits(client_ip, session_id):
    current_time = time.time()
    with rate_limit_lock:
        if session_counts[client_ip] >= APP_CONNECTION_MAX_SESSIONS_PER_IP:
            return False, "Too many sessions from this IP"
        request_counts[session_id] = [t for t in request_counts[session_id] if current_time - t < 60]
        if len(request_counts[session_id]) >= APP_CONNECTION_MAX_REQUESTS_PER_MINUTE:
            return False, "Rate limit exceeded"
        request_counts[session_id].append(current_time)
        return True, ""


async def get_bot_response(user_message: str, session_id: str) -> AIResponse:
    logger.debug(f"get_bot_response enter: {user_message} {session_id}")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-api-key": f"{APP_API_KEY}"
    }
    api_url = f"http://{APP_API_HOST}:{APP_API_PORT}/api/chat"
    payload = {
        "message": user_message,
        "session_id": session_id
    }

    try:
        # Use aiohttp for async HTTP request
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    api_url,
                    data=json.dumps(payload),
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)  # 30-second total timeout
            ) as response:
                # Raise exception for bad status codes
                response.raise_for_status()
                # Get JSON data
                json_data = await response.json()

                ai_response = AIResponse(
                    message=json_data.get("message", "No message received"),
                    suggestions=json_data.get("suggestions", [])
                )
                logger.debug(f"get_bot_response exit: {user_message} {session_id}")
                return ai_response

    except Exception as e:
        logger.error(f"Error in bot response: {e}")
        return AIResponse(message="Sorry, something went wrong", suggestions=[])


def get_bot_suggestions(response: AIResponse) -> str:
    return "\n".join(f"- {suggestion}" for suggestion in response.suggestions) if response.suggestions else ""


# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_ip = websocket.client.host
    token = websocket.query_params.get("token")
    logger.debug(f"WebSocket connect attempt - IP: {client_ip}, Token: {token}")

    # Validate connection
    if not token:
        await websocket.send_json({"error": {"message": "No session token provided"}})
        await websocket.close()
        return

    valid, result = validate_token(token, client_ip)
    if not valid:
        await websocket.send_json({"error": {"message": result}})
        await websocket.close()
        return

    session_id = result
    session = {"session_id": session_id}  # Simple session storage for this example
    logger.info(f"Client connected - IP: {client_ip}, Session: {session_id}")

    allowed, message = check_rate_limits(client_ip, session_id)
    if not allowed:
        await websocket.send_json({"error": {"message": message}})
        await websocket.close()
        return

    # Send initial chat history
    await websocket.send_json({
        "message": {"chat_history": chat_history.get_history(session_id)}
    })

    try:
        while True:
            data = await websocket.receive_json()
            logger.debug(f"handle_message enter: {client_ip} {session_id}")

            allowed, message = check_rate_limits(client_ip, session_id)
            if not allowed:
                await websocket.send_json({"error": {"message": message}})
                continue

            user_message = data.get("message", "").strip()
            if not user_message:
                continue

            try:
                bot_response = await get_bot_response(user_message, session_id)
                chat_history.append(session_id, {"sender": "You", "text": user_message})
                chat_history.append(session_id, {"sender": "Bot", "json": bot_response})
                logger.debug(f"Message processed - User: {user_message}, Bot: {bot_response.message}")

                await websocket.send_json({
                    "user_message": user_message,
                    "bot_response": bot_response.message,
                    "bot_suggestions": get_bot_suggestions(bot_response)
                })
            except Exception as e:
                logger.error(f"Error handling message: {e}")
                await websocket.send_json({"error": {"message": "Internal server error"}})

            logger.debug(f"handle_message exit: {client_ip} {session_id}")

    except WebSocketDisconnect:
        logger.debug(f"handle_disconnect enter: {client_ip} {session_id}")
        if session_id:
            chat_history.clear_history(session_id)
            with rate_limit_lock:
                session_counts[client_ip] = max(0, session_counts[client_ip] - 1)
        logger.info(f"Client disconnected - IP: {client_ip}, Session: {session_id}")


if __name__ == "__main__":
    PORT = int(APP_WS_PORT)
    APP_WS_HOST = "0.0.0.0"
    try:
        if APP_ENV == "dev":
            logger.info(f"Starting dev server on port {PORT}")
            uvicorn.run(
                "main:app",  # Assuming this file is named main.py
                host=APP_WS_HOST,
                port=PORT,
                reload=True,  # Auto-reload on code changes
                log_level="debug",  # Detailed logging
                workers=1  # Single worker for development
            )
        else:
            uvicorn.run(
                "main:app",  # Assuming this file is named main.py
                host=APP_WS_HOST,  # Listen on all interfaces
                port=PORT,  # Use PORT env var or default to 8000
                reload=False,  # No auto-reload in production
                log_level="info",  # Less verbose logging
                workers=2,  # Number of workers from env var, default 4
                timeout_keep_alive=120,  # Longer keep-alive for production
            )
    except PermissionError:
        logger.error(f"Permission denied on port {PORT}")
        raise
    except Exception as e:
        logger.error(f"Server startup failed: {e}")
        raise
