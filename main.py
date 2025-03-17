from dotenv import load_dotenv
from gevent import monkey
monkey.patch_all()

import json
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional, List

from flask import Flask, session, request, jsonify
from flask_cors import CORS  # Import Flask-CORS
from flask_socketio import SocketIO, emit
from pydantic import BaseModel


from src.chatagent_ws.LoggingUtil import *
from src.chatagent_ws.AppConfig import APP_CONNECTION_MAX_SESSIONS_PER_IP, \
    APP_CONNECTION_MAX_REQUESTS_PER_MINUTE, APP_SECURITY_TOKEN_EXPIRY_SECONDS, APP_API_HOST, APP_API_PORT, \
    APP_WS_PORT, APP_WS_HOST, APP_API_KEY, APP_WS_API_KEY, APP_ENV

logger = get_logger("chatagent-ws")

load_dotenv()

# Configuration
app = Flask(__name__)
app.config["SECRET_KEY"] = "your-secret-key-here"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Enable CORS with credentials support
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

# Token storage and configuration
session_tokens = {}
token_lock = Lock()

# socketio = SocketIO(app, async_mode='gevent')

socketio = SocketIO(app,
                    engineio_logger=False,
                    cors_allowed_origins="*",
                    async_mode='gevent',
                    ping_timeout=30,
                    ping_interval=10)

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


def validate_token(token, client_ip):
    with token_lock:
        if token not in session_tokens:
            return False, "Invalid token"
        token_info = session_tokens[token]
        if token_info["ip"] != client_ip:
            return False, "Token IP mismatch"
        if token_info["expiry"] < datetime.now():
            del session_tokens[token]
            return False, "Token expired"
        return True, token_info["session_id"]


@app.route('/api/get_session_token', methods=['POST'])
def get_session_token():
    logger.debug(f"get_session_token enter")
    # Check for API key in headers
    api_key = request.headers.get('x-api-key')
    if not api_key:
        return jsonify({"error": "API key is required"}), 401
    if not validate_api_key(api_key):
        return jsonify({"error": "Invalid API key"}), 401

    client_ip = request.remote_addr
    with rate_limit_lock:
        if session_counts[client_ip] >= APP_CONNECTION_MAX_SESSIONS_PER_IP:
            return jsonify({"error": "Too many sessions from this IP"}), 429

    session_id = str(uuid.uuid4())
    token = generate_session_token(session_id, client_ip)
    with rate_limit_lock:
        session_counts[client_ip] += 1

    logger.debug(f"get_session_token exit")
    return jsonify({
        "token": token,
        "expires_in": APP_SECURITY_TOKEN_EXPIRY_SECONDS,
        "session_id": session_id
    })


def validate_api_key(api_key):
    """
    Validate the API key against a stored value or database.
    Implement this according to your needs.
    """
    # Example implementation - replace with your actual validation logic
    return api_key == APP_WS_API_KEY


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


def get_bot_response(user_message, session_id):
    logger.debug(f"get_bot_response enter: {user_message} {session_id}")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",  # Change if API returns different content type
        "x-api-key": f"{APP_API_KEY}"
    }
    import requests
    api_url = f"http://{APP_API_HOST}:{APP_API_PORT}/api/chat"
    payload = {
        "message": user_message,
        "session_id": session_id
    }
    try:
        # Make the POST request
        response = requests.post(api_url,
                                 data=json.dumps(payload),
                                 headers=headers,
                                 timeout=30)  # 5-second timeout
        # Check if request was successful
        response.raise_for_status()
        json_data = json.loads(response.text)
        ai_response = AIResponse(
            message=json_data.get("message"),  # Use .get() to avoid KeyError if key is missing
            suggestions=json_data.get("suggestions")
        )
        logger.debug(f"get_bot_response exit: {user_message} {session_id}")
        return ai_response
    except Exception as e:
        logger.error(f"Error in bot response: {e}")
        return AIResponse(message="Sorry, something went wrong", suggestions=[])


def get_bot_suggestions(response: AIResponse) -> str:
    return "\n".join(f"- {suggestion}" for suggestion in response.suggestions) if response.suggestions else ""


@socketio.on("connect")
def handle_connect():
    client_ip = request.remote_addr
    token = request.args.get("token")
    logger.debug(f"WebSocket connect attempt - IP: {client_ip}, Token: {token}")
    if not token:
        emit("error", {"message": "No session token provided"})
        return False
    valid, result = validate_token(token, client_ip)
    if not valid:
        emit("error", {"message": result})
        return False
    session_id = result
    session["session_id"] = session_id
    logger.info(f"Client connected - IP: {client_ip}, Session: {session_id}")
    allowed, message = check_rate_limits(client_ip, session_id)
    if not allowed:
        emit("error", {"message": message})
        return False
    emit("message", {"chat_history": chat_history.get_history(session_id)})
    return True


@socketio.on("disconnect")
def handle_disconnect():
    client_ip = request.remote_addr
    session_id = session.get("session_id")
    logger.debug(f"handle_disconnect enter: {client_ip} {session_id}")
    if session_id:
        chat_history.clear_history(session_id)
        with rate_limit_lock:
            session_counts[client_ip] = max(0, session_counts[client_ip] - 1)
    logger.info(f"Client disconnected - IP: {client_ip}, Session: {session_id}")


@socketio.on("message")
def handle_message(data):
    client_ip = request.remote_addr
    session_id = session["session_id"]
    logger.debug(f"handle_message enter: {client_ip} {session_id}")
    allowed, message = check_rate_limits(client_ip, session_id)
    if not allowed:
        emit("error", {"message": message})
        return
    user_message = data.get("message", "").strip()
    if not user_message:
        return
    try:
        bot_response = get_bot_response(user_message, session_id)
        chat_history.append(session_id, {"sender": "You", "text": user_message})
        chat_history.append(session_id, {"sender": "Bot", "json": bot_response})
        logger.debug(f"Message processed - User: {user_message}, Bot: {bot_response.message}")
        emit("message", {
            "user_message": user_message,
            "bot_response": bot_response.message,
            "bot_suggestions": get_bot_suggestions(bot_response)
        }, broadcast=True)
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        emit("error", {"message": "Internal server error"})
    logger.debug(f"handle_message exit: {client_ip} {session_id}")

if __name__ == "__main__":
    port = int(APP_WS_PORT)
    try:
        if APP_ENV == "dev":
            logger.info(f"Starting dev server on port {port}")
            socketio.run(app, host=APP_WS_HOST, port=port, debug=True, use_reloader=False, log_output=True)
        else:
            # Production mode (no socketio.run)
            logger.info(f"Application ready for production on {APP_WS_HOST}:{port}")
    except PermissionError:
        logger.error(f"Permission denied on port {port}")
        raise
    except Exception as e:
        logger.error(f"Server startup failed: {e}")
        raise
