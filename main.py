import asyncio
import json
import secrets
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, List, Annotated

import aiohttp
import redis.asyncio as redis
import time
import uvicorn
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.chatagent_ws.AppConfig import (
    APP_CONNECTION_MAX_SESSIONS_PER_IP,
    APP_CONNECTION_MAX_REQUESTS_PER_MINUTE,
    APP_SECURITY_TOKEN_EXPIRY_SECONDS,
    APP_API_HOST,
    APP_API_PORT,
    APP_WS_PORT,
    APP_API_KEY,
    APP_WS_API_KEY,
    APP_ENV,
    APP_REDIS_HOST,
    APP_REDIS_PORT,
    APP_REDIS_DB,
    APP_REDIS_PASSWORD, APP_WS_TIMEOUT_SECONDS, APP_WS_ALLOWED_ORIGIN
)

from src.chatagent_ws.LoggingUtil import *

load_dotenv()
logger = get_logger("chatagent-ws")

# Define APP_WS_HOST globally (move it out of __main__)
APP_WS_HOST = "0.0.0.0"

# Redis client setup with password
redis_client = redis.Redis(
    host=APP_REDIS_HOST,
    port=APP_REDIS_PORT,
    db=APP_REDIS_DB,
    password=APP_REDIS_PASSWORD,
    decode_responses=True
)


# Lifespan event handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting up")
    try:
        await redis_client.ping()
        logger.info("Successfully connected to Redis")
    except redis.AuthenticationError:
        logger.error("Redis authentication failed")
        raise
    except redis.ConnectionError as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise
    yield
    await redis_client.close()
    logger.info("Application shutting down")


# Configuration
app = FastAPI(
    title="Chat Agent WebSocket API",
    description="WebSocket-based chat service with HTTP and WebSocket endpoints",
    version="1.0.0",
    lifespan=lifespan
)

allowed_origins = [
    APP_WS_ALLOWED_ORIGIN,  # Explicitly allow your client origin
    "http://localhost:8000",     # For local development (optional)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,  # List of trusted origins
    allow_credentials=True,         # Allow cookies/credentials
    allow_methods=["*"],            # Allow all methods (GET, POST, etc.)
    allow_headers=["*"],            # Allow all headers (e.g., x-api-key)
)

# Rate limiting configuration
request_counts = defaultdict(list)


class AIResponse(BaseModel):
    message: str
    suggestions: Optional[List[str]] = None
    sources: Optional[List[str]] = None


class ChatHistory:
    def __init__(self):
        self.redis = redis_client

    async def get_history(self, session_id: str) -> List[dict]:
        history = await self.redis.lrange(f"chat:{session_id}", 0, -1)
        return [json.loads(msg) for msg in history] if history else []

    async def clear_history(self, session_id: str):
        await self.redis.delete(f"chat:{session_id}")

    async def append(self, session_id: str, message: dict):
        await self.redis.rpush(f"chat:{session_id}", json.dumps(message))
        await self.redis.expire(f"chat:{session_id}", 86400)


chat_history = ChatHistory()


async def generate_session_token(session_id: str, client_ip: str) -> str:
    token = secrets.token_urlsafe(32)
    token_data = {
        "expiry": (datetime.now() + timedelta(seconds=APP_SECURITY_TOKEN_EXPIRY_SECONDS)).isoformat(),
        "session_id": session_id,
        "ip": client_ip
    }
    await redis_client.setex(
        f"token:{token}",
        APP_SECURITY_TOKEN_EXPIRY_SECONDS,
        json.dumps(token_data)
    )
    return token


async def verify_api_key(x_api_key: Annotated[str, Header()], request: Request):
    client_ip = request.client.host
    logger.debug(f"x_api_key:{x_api_key}")
    logger.debug(f"APP_WS_API_KEY:{APP_WS_API_KEY}")
    if x_api_key != APP_WS_API_KEY:
        logger.warning(f"AUDIT: Invalid APP_API_KEY from IP {client_ip}")
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key


async def validate_token(token: str, client_ip: str) -> tuple[bool, str]:
    token_data = await redis_client.get(f"token:{token}")
    if not token_data:
        logger.warning(f"AUDIT: Invalid token {token} from IP {client_ip}")
        return False, "Invalid token"

    token_info = json.loads(token_data)
    if token_info["ip"] != client_ip:
        logger.warning(f"AUDIT: Token IP mismatch for {token} from IP {client_ip}")
        return False, "Token IP mismatch"
    if datetime.fromisoformat(token_info["expiry"]) < datetime.now():
        await redis_client.delete(f"token:{token}")
        logger.info(f"AUDIT: Token {token} expired from IP {client_ip}")
        return False, "Token expired"
    return True, token_info["session_id"]


@app.post("/api/get_session_token")
async def get_session_token(request: Request, api_key: str = Depends(verify_api_key)):
    client_ip = request.client.host
    session_count = await redis_client.get(f"sessions:{client_ip}")
    session_count = int(session_count or 0)

    if session_count >= APP_CONNECTION_MAX_SESSIONS_PER_IP:
        logger.info(f"AUDIT: Exceeded {APP_CONNECTION_MAX_SESSIONS_PER_IP} sessions from IP {client_ip}")
        raise HTTPException(status_code=429, detail="Too many sessions from this IP")

    session_id = str(uuid.uuid4())
    token = await generate_session_token(session_id, client_ip)

    await redis_client.incr(f"sessions:{client_ip}")
    await redis_client.expire(f"sessions:{client_ip}", APP_SECURITY_TOKEN_EXPIRY_SECONDS)

    return {
        "token": token,
        "expires_in": APP_SECURITY_TOKEN_EXPIRY_SECONDS,
        "session_id": session_id
    }


async def check_rate_limits(client_ip: str, session_id: str) -> tuple[bool, str]:
    current_time = time.time()
    key = f"rate:{session_id}"

    await redis_client.zremrangebyscore(key, 0, current_time - 60)
    count = await redis_client.zcard(key)

    if count >= APP_CONNECTION_MAX_REQUESTS_PER_MINUTE:
        return False, "Rate limit exceeded"

    await redis_client.zadd(key, {str(current_time): current_time})
    await redis_client.expire(key, 60)

    session_count = await redis_client.get(f"sessions:{client_ip}")
    if int(session_count or 0) >= APP_CONNECTION_MAX_SESSIONS_PER_IP:
        return False, "Too many sessions from this IP"

    return True, ""


async def get_bot_response(user_message: str, session_id: str) -> AIResponse:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-api-key": APP_API_KEY
    }
    api_url = f"http://{APP_API_HOST}:{APP_API_PORT}/api/chat"
    payload = {"message": user_message, "session_id": session_id}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    api_url,
                    data=json.dumps(payload),
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                response.raise_for_status()
                json_data = await response.json()
                return AIResponse(
                    message=json_data.get("message", "No message received"),
                    suggestions=json_data.get("suggestions", [])
                )
    except aiohttp.ClientError as e:
        logger.error(f"API request failed: {e}")
        raise HTTPException(status_code=503, detail="Backend service unavailable")
    except Exception as e:
        logger.error(f"Unexpected error in bot response: {e}")
        return AIResponse(message="Sorry, something went wrong", suggestions=[])


def get_bot_suggestions(response: AIResponse) -> str:
    return "\n".join(f"- {suggestion}" for suggestion in response.suggestions) if response.suggestions else ""


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_ip = websocket.client.host
    token = websocket.query_params.get("token")
    logger.debug(f"websocket_endpoint received token")
    if not token:
        await websocket.send_json({"error": "No session token provided"})
        await websocket.close(code=1008)
        return

    valid, result = await validate_token(token, client_ip)
    logger.debug(f"websocket_endpoint received token valid: {valid}")
    if not valid:
        await websocket.send_json({"error": result})
        await websocket.close(code=1008)
        return

    session_id = result
    logger.info(f"Client connected - IP: {client_ip}, Session: {session_id}")

    allowed, message = await check_rate_limits(client_ip, session_id)
    if not allowed:
        await websocket.send_json({"error": message})
        await websocket.close(code=1008)
        return

    await websocket.send_json({"chat_history": await chat_history.get_history(session_id)})

    try:
        while True:
            data = await websocket.receive_json()
            logger.debug(f"websocket_endpoint received client message: {data}")
            allowed, message = await check_rate_limits(client_ip, session_id)
            if not allowed:
                await websocket.send_json({"error": message})
                continue

            user_message = data.get("message", "").strip()
            if not user_message:
                continue

            bot_response = await get_bot_response(user_message, session_id)
            await chat_history.append(session_id, {"sender": "You", "text": user_message})
            await chat_history.append(session_id, {"sender": "Bot", "json": bot_response.dict()})

            await websocket.send_json({
                "user_message": user_message,
                "bot_response": bot_response.message,
                "bot_suggestions": get_bot_suggestions(bot_response)
            })
    except asyncio.TimeoutError:
        logger.debug(f"Client IP {client_ip}, Session {session_id} timed out after {APP_WS_TIMEOUT_SECONDS}s of inactivity")
        await websocket.send_json({"error": "Connection timed out due to inactivity"})
        await websocket.close(code=1000, reason="Inactivity timeout")
    except WebSocketDisconnect:
        await chat_history.clear_history(session_id)
        await redis_client.decr(f"sessions:{client_ip}")
        logger.info(f"Client disconnected - IP: {client_ip}, Session: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close(code=1011)


# Custom OpenAPI schema to include WebSocket documentation
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    # Call the original FastAPI openapi method to avoid recursion
    from fastapi import FastAPI as FastAPIClass
    openapi_schema = FastAPIClass.openapi(app)

    # Add WebSocket endpoint description with fallback values
    try:
        openapi_schema["paths"]["/ws"] = {
            "get": {
                "summary": "WebSocket Chat Endpoint",
                "description": """
                     WebSocket endpoint for real-time chat. Connect using a WebSocket client with the following parameters:
                     - **URL**: `ws://{APP_WS_HOST}:{APP_WS_PORT}/ws?token=<session_token>`
                     - **Query Parameter**: `token` (obtained from `/api/get_session_token`)
                     - **Message Format**: JSON object with `message` field (e.g., `{{"message": "Hello"}}`)
                     - **Response Format**: JSON with `user_message`, `bot_response`, and `bot_suggestions`
                     - **Rate Limits**: {APP_CONNECTION_MAX_REQUESTS_PER_MINUTE} requests per minute, {APP_CONNECTION_MAX_SESSIONS_PER_IP} sessions per IP
                 """.format(
                    APP_WS_HOST=APP_WS_HOST,
                    APP_WS_PORT=APP_WS_PORT,
                    APP_CONNECTION_MAX_REQUESTS_PER_MINUTE=APP_CONNECTION_MAX_REQUESTS_PER_MINUTE,
                    APP_CONNECTION_MAX_SESSIONS_PER_IP=APP_CONNECTION_MAX_SESSIONS_PER_IP
                ),
                "responses": {
                    "101": {"description": "Switching Protocols (WebSocket handshake successful)"},
                    "400": {"description": "Invalid token or rate limit exceeded"}
                },
                "operationId": "websocket_chat_ws",
                "x-websocket": True
            }
        }
    except (NameError, AttributeError) as e:
        logger.error(f"Error formatting WebSocket docs: {e}")
        openapi_schema["paths"]["/ws"] = {
            "get": {
                "summary": "WebSocket Chat Endpoint",
                "description": "WebSocket endpoint for real-time chat. Connect using a WebSocket client with a token from `/api/get_session_token`.",
                "responses": {
                    "101": {"description": "Switching Protocols (WebSocket handshake successful)"},
                    "400": {"description": "Invalid token or rate limit exceeded"}
                },
                "operationId": "websocket_chat_ws",
                "x-websocket": True
            }
        }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

if __name__ == "__main__":
    PORT = int(APP_WS_PORT)
    config = uvicorn.Config(
        "main:app",
        host=APP_WS_HOST,
        port=PORT,
        log_level="info",
        workers=2 if APP_ENV != "dev" else 1,
        reload=APP_ENV == "dev",
        timeout_keep_alive=APP_WS_TIMEOUT_SECONDS
    )
    server = uvicorn.Server(config)

    try:
        logger.info(f"Starting server on {APP_WS_HOST}:{PORT} in {APP_ENV} mode with timeout {APP_WS_TIMEOUT_SECONDS}")
        server.run()
    except Exception as e:
        logger.error(f"Server startup failed: {e}")
        raise