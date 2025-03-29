import uuid
from contextlib import asynccontextmanager
from typing import Dict

import redis.asyncio as redis
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Request, Body
from fastapi.middleware.cors import CORSMiddleware

from chatagent_ws.LoggingUtil import get_logger
from chatagent_ws.session_manager import session_redis_client, generate_session_token, verify_api_key, validate_token
from chatagent_ws.AppConfig import (
    APP_CONNECTION_MAX_SESSIONS_PER_IP,
    APP_SECURITY_TOKEN_EXPIRY_SECONDS,
    APP_WS_PORT,
    APP_ENV,
    APP_WS_TIMEOUT_SECONDS,
    APP_WS_ALLOWED_ORIGIN
)
from chatagent_ws.ws_speech import websocket_speech_endpoint
from chatagent_ws.ws_text import websocket_text_endpoint

load_dotenv()
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting up")
    try:
        await session_redis_client.ping()
        logger.info("Successfully connected to Redis")
    except redis.AuthenticationError as e:
        logger.error(f"Redis authentication failed: {e}")
        raise
    except redis.ConnectionError as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise
    yield
    await session_redis_client.close()
    logger.info("Application shutting down")


# Validate configuration
if not all([APP_WS_PORT, APP_WS_ALLOWED_ORIGIN, APP_SECURITY_TOKEN_EXPIRY_SECONDS]):
    raise ValueError("Missing required configuration values")

app = FastAPI(
    title="Chat Agent WebSocket API",
    description="WebSocket-based chat service with HTTP and WebSocket endpoints",
    version="1.0.0",
    lifespan=lifespan
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[APP_WS_ALLOWED_ORIGIN, "http://localhost:8000", "http://localhost:8002"],
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["X-API-Key", "X-Session-Token", "X-Session-Id", "Content-Type"]
)

app.websocket("/speech-ws")(websocket_speech_endpoint)
app.websocket("/text-ws")(websocket_text_endpoint)


@app.post("/api/get_session_token")
async def get_session_token(request: Request, api_key: str = Depends(verify_api_key)) -> Dict[str, str | int]:
    client_ip = request.client.host
    session_count_key = f"session/ip:{client_ip}"

    try:
        session_id = str(uuid.uuid4())
        token = await generate_session_token(session_id, client_ip)

        await session_redis_client.incr(session_count_key)
        await session_redis_client.expire(session_count_key, APP_SECURITY_TOKEN_EXPIRY_SECONDS)

        return {
            "session_token": token,
            "expires_in": APP_SECURITY_TOKEN_EXPIRY_SECONDS
        }
    except redis.RedisError as e:
        logger.error(f"Redis access failed: {e}")
        raise HTTPException(status_code=503, detail="Service unavailable")
    except Exception as e:
        logger.error(f"Session token generation failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/api/refresh_session_token")
async def refresh_session_token(
        request: Request,
        current_token: str = Body(..., embed=True),
        api_key: str = Depends(verify_api_key)
) -> Dict[str, str | int]:
    client_ip = request.client.host
    session_count_key = f"session/ip:{client_ip}"

    try:
        # Verify existing token and extract session_id
        try:
            if_validate, session_id = await validate_token(current_token, client_ip)
            if not if_validate:
                raise ValueError("Invalid token payload")
        except Exception as e:
            logger.info(f"AUDIT: Invalid token refresh attempt from IP {client_ip}: {e}")
            raise HTTPException(status_code=401, detail="Invalid or expired session token")

        # Generate new token with existing session_id
        new_token = await generate_session_token(session_id, client_ip)

        # Reset expiry for session count key
        await session_redis_client.expire(session_count_key, APP_SECURITY_TOKEN_EXPIRY_SECONDS)

        return {
            "session_token": new_token,
            "expires_in": APP_SECURITY_TOKEN_EXPIRY_SECONDS
        }
    except HTTPException as e:
        raise e
    except redis.RedisError as e:
        logger.error(f"Redis access failed: {e}")
        raise HTTPException(status_code=503, detail="Service unavailable")
    except Exception as e:
        logger.error(f"Session token refresh failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy"}


async def main():
    APP_WS_HOST = "0.0.0.0"
    try:
        PORT = int(APP_WS_PORT)
    except ValueError:
        logger.error(f"Invalid port value: {APP_WS_PORT}")
        raise

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

    logger.info(f"Starting server on {APP_WS_HOST}:{PORT} in {APP_ENV} mode with timeout {APP_WS_TIMEOUT_SECONDS}")
    await server.serve()


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.error(f"Server startup failed: {e}")
        raise
