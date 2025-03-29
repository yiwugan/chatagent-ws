import json
import secrets
from datetime import datetime, timedelta
from typing import Annotated

import redis.asyncio as redis
import time
from dotenv import load_dotenv
from fastapi import HTTPException, Header, Request
from starlette.websockets import WebSocket

from app_config import (
    APP_CONNECTION_MAX_SESSIONS_PER_IP,
    APP_CONNECTION_MAX_REQUESTS_PER_MINUTE,
    APP_WS_API_KEY
)
from app_config import APP_REDIS_HOST, APP_REDIS_PORT, APP_REDIS_DB, APP_REDIS_PASSWORD, \
    APP_SECURITY_TOKEN_EXPIRY_SECONDS
from logging_util import get_logger

load_dotenv()
logger = get_logger("session_manager")

# Redis client setup with password
session_redis_client = redis.Redis(
    host=APP_REDIS_HOST,
    port=APP_REDIS_PORT,
    db=APP_REDIS_DB,
    password=APP_REDIS_PASSWORD,
    decode_responses=True
)

def get_client_ip_from_websocket(websocket: WebSocket):
    try:
        request = websocket._scope.get("request") or websocket.scope
        client_ip = request.headers.get("X-Forwarded-For")
        if client_ip:
            # X-Forwarded-For may contain a comma-separated list (client IP is first)
            client_ip = client_ip.split(",")[0].strip()
        else:
            # Fallback to direct client host if header is missing (unlikely with ALB)
            client_ip = "unknown"
        return client_ip
    except Exception as e:
        return "unknown"


def get_client_ip_from_request(request:Request):
    try:
        client_ip = request.headers.get("X-Forwarded-For")
        if client_ip:
            # X-Forwarded-For may contain a comma-separated list (client IP is first)
            client_ip = client_ip.split(",")[0].strip()
        else:
            # Fallback to direct client host if header is missing (unlikely with ALB)
            client_ip = "unknown"
        return client_ip
    except Exception as e:
        return "unknown"

async def generate_session_token(session_id: str, client_ip: str) -> str:
    token = secrets.token_urlsafe(32)
    token_data = {
        "expiry": (datetime.now() + timedelta(seconds=APP_SECURITY_TOKEN_EXPIRY_SECONDS)).isoformat(),
        "session_id": session_id,
        "ip": client_ip
    }
    await session_redis_client.setex(
        f"session/token:{token}",
        APP_SECURITY_TOKEN_EXPIRY_SECONDS,
        json.dumps(token_data)
    )
    return token


async def check_rate_limits(client_ip: str, session_id: str) -> tuple[bool, str]:
    current_time = time.time()
    key = f"session/rate:{session_id}"

    await session_redis_client.zremrangebyscore(key, 0, current_time - 60)
    count = await session_redis_client.zcard(key)

    if count >= APP_CONNECTION_MAX_REQUESTS_PER_MINUTE:
        return False, "Rate limit exceeded"

    await session_redis_client.zadd(key, {str(current_time): current_time})
    await session_redis_client.expire(key, 60)

    session_count = await session_redis_client.get(f"session/ip:{client_ip}")

    return True, ""
    # if int(session_count or 0) >= APP_CONNECTION_MAX_SESSIONS_PER_IP:
    #     return False, "Too many sessions from this IP"
    #
    # return True, ""


async def validate_token(token: str, client_ip: str) -> tuple[bool, str]:
    token_data = await session_redis_client.get(f"session/token:{token}")
    if not token_data:
        logger.warning(f"AUDIT: Invalid token {token} from IP {client_ip}")
        return False, "Invalid token"

    token_info = json.loads(token_data)
    if token_info["ip"] != client_ip:
        logger.warning(f"AUDIT: Token IP mismatch for {token} from IP {client_ip}")
        return False, "Token IP mismatch"
    if datetime.fromisoformat(token_info["expiry"]) < datetime.now():
        await session_redis_client.delete(f"session/token:{token}")
        logger.info(f"AUDIT: Token {token} expired from IP {client_ip}")
        return False, "Token expired"
    return True, token_info["session_id"]


async def verify_api_key(x_api_key: Annotated[str, Header()], request: Request):
    client_ip = request.client.host
    logger.debug(f"x_api_key:{x_api_key}")
    logger.debug(f"APP_WS_API_KEY:{APP_WS_API_KEY}")
    if x_api_key != APP_WS_API_KEY:
        logger.warning(f"AUDIT: Invalid APP_API_KEY from IP {client_ip}")
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key
