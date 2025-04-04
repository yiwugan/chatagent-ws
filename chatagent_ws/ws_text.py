import asyncio
import json
import logging
from typing import AsyncIterator, Optional, Dict
from urllib.parse import parse_qs

# import nltk
from dotenv import load_dotenv
from fastapi import WebSocket, WebSocketDisconnect
from httpx import AsyncClient, TimeoutException, RequestError, HTTPStatusError

from app_config import APP_API_HOST, APP_API_PORT, APP_WS_IDLE_TIMEOUT_SECONDS
from language_util import fix_markdown_list_spacing
from logging_util import get_logger
from session_manager import validate_token, check_rate_limits, get_client_ip_from_websocket

load_dotenv()

# NLTK setup
# try:
#     nltk.data.find('tokenizers/punkt')
# except LookupError:
#     nltk.download('punkt')

logger = get_logger("ws_text")
logging.basicConfig(level=logging.INFO)

MAX_BUFFER_SIZE = 1024 * 1024  # 1MB buffer limit
MAX_INPUT_SIZE = 10 * 1024  # 10KB input limit


async def call_api(
        message: str,
        x_session_id: str,
        base_url: str = f"{APP_API_HOST}:{APP_API_PORT}",
        timeout: float = 60.0,
        headers: Optional[Dict[str, str]] = None,
) -> AsyncIterator[str]:
    """
    Calls the streaming API, handling potential errors and timeouts.

    Args:
        message: The message to send to the API.
        x_session_id: The session ID.
        base_url: The base URL of the API.
        timeout: The timeout for the API call.
        headers: Optional headers to include in the request.

    Returns:
        An asynchronous iterator yielding chunks of the response text.

    Raises:
        Exception: If the API call fails or times out.
    """
    logger.info("Calling streaming API")
    url = f"{base_url}/api/chat/streaming"  # Changed to chat endpoint
    payload = {"message": message}
    default_headers = {
        "X-Session-Id": x_session_id,
        "Content-Type": "application/json",
    }
    if headers:
        default_headers.update(headers)

    async with AsyncClient() as client:
        try:
            async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=default_headers,
                    timeout=timeout,
            ) as response:
                logger.info("receiving from streaming API")
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    logger.info(f"receiving from streaming API {chunk}")
                    yield chunk
                logger.info(f"receiving from streaming API done")
        except (TimeoutException, RequestError, HTTPStatusError) as e:
            logger.error(f"API call failed: {e}")
            raise


async def process_input(user_input: str, websocket: WebSocket, session_id: str):
    logger.info("Processing input")
    if len(user_input) > MAX_INPUT_SIZE:
        await websocket.send_json({
            "type": "stream_error",
            "text": "Input exceeds maximum size limit"
        })
        return

    data = json.loads(user_input)
    text_input = data.get("text", "").strip()

    if not text_input:
        await websocket.send_json({
            "type": "stream_error",
            "text": "Empty input received"
        })
        return

    try:
        buffer = ""
        async for chunk in call_api(text_input, session_id):
            logger.info(f"receiving from streaming API {chunk}")
            # if len(buffer) + len(chunk) > MAX_BUFFER_SIZE:
            #     logger.error("Buffer size exceeded")
            #     await websocket.send_json({
            #         "type": "stream_error",
            #         "text": "Response too large"
            #     })
            #     return
            #
            # buffer += chunk

            # Send chunks as they come, respecting sentence boundaries
            if chunk == "[DONE]":
                # if buffer.strip():
                #     # Remove [DONE] and send final buffer
                #     buffer = buffer.replace("[DONE]", "").strip()
                #     if buffer:
                #         await websocket.send_json({
                #             "type": "response_chunk",
                #             "text": buffer,
                #             "session_id": session_id
                #         })
                await websocket.send_json({"type": "response_end"})
                break
            elif "Error:" in chunk:
                await websocket.send_json({"type": "stream_error", "text": chunk})
                return
            else:
                # send chunk directly
                fixed_chunk=fix_markdown_list_spacing(chunk.strip())
                await websocket.send_json({
                    "type": "response_chunk",
                    "text": fixed_chunk
                    # "session_id": session_id
                })

                # Send complete sentences when possible
                # sentences = sent_tokenize(buffer)
                # for i, sentence in enumerate(sentences[:-1]):  # All but the last (incomplete) sentence
                #     await websocket.send_json({
                #         "type": "response_chunk",
                #         "text": sentence.strip(),
                #         "session_id": session_id
                #     })
                # buffer = sentences[-1] if sentences else buffer  # Keep incomplete sentence

    except Exception as e:
        logger.error(f"Processing error: {e}")
        await websocket.send_json({"type": "stream_error", "text": str(e)})


async def websocket_text_endpoint(websocket: WebSocket):
    """
    Handles a WebSocket connection, including a connection
    idle check.  If the client is idle for more than 10 minutes, the
    connection is closed.

    Args:
        websocket: The WebSocket connection object.
    """
    await websocket.accept()
    logger.info("websocket_text_endpoint connection established")

    last_activity = asyncio.get_event_loop().time()  # Track last activity time
    idle_timeout = APP_WS_IDLE_TIMEOUT_SECONDS  # 10 minutes in seconds

    async def check_idle():
        """
        Periodically checks if the connection has been idle for too long
        and closes it if necessary.
        """
        nonlocal last_activity  # Access the last_activity variable from the outer scope
        while True:
            now = asyncio.get_event_loop().time()
            if now - last_activity > idle_timeout:
                logger.info("websocket_text_endpoint Connection idle timeout exceeded. Closing connection.")
                await websocket.close(code=1001, reason="Idle timeout")  # Use 1001 for going away
                return  # Exit the idle check task
            await asyncio.sleep(60)  # Check every 60 seconds (adjust as needed)

    # Start the idle check task
    idle_check_task = asyncio.create_task(check_idle())

    try:
        # session token at connection may be different from token in payload
        # since token may be expired and refreshed during connection
        connection_session_token = parse_qs(websocket.url.query).get("session_token", [None])[0]

        if not connection_session_token:
            await websocket.send_json({
                "type": "stream_error",
                "text": "Missing session_token"
            })
            await websocket.close(code=1002, reason="Missing session token")
            return

        client_ip = get_client_ip_from_websocket(websocket)
        is_valid, session_id = await validate_token(connection_session_token, client_ip)
        if not is_valid:
            await websocket.send_json({
                "type": "stream_error",
                "text": "Session_token invalid or expired"
            })
            await websocket.close(code=1002, reason="Invalid session token")
            return

        is_in_ratelimit, result = await check_rate_limits(client_ip, connection_session_token)
        if not is_in_ratelimit:
            await websocket.send_json({
                "type": "stream_error",
                "text": f"{result}"
            })
            await websocket.close(code=1002, reason="Rate limit exceeded")
            return

        # Send a message to the client to indicate successful connection and session validation
        #        await websocket.send_json({"type": "connection_success", "session_id": session_id})

        while True:
            message = await websocket.receive_text()
            last_activity = asyncio.get_event_loop().time()  # update last_activity
            data = json.loads(message)
            if data.get("type") == "userInput":
                session_token = data.get("session_token")
                if not session_token:
                    await websocket.send_json({
                        "type": "stream_error",
                        "text": "Missing session_token"
                    })
                    continue
                client_ip = get_client_ip_from_websocket(websocket)
                is_valid, session_id = await validate_token(session_token, client_ip)
                if not is_valid:
                    await websocket.send_json({
                        "type": "stream_error",
                        "text": "Session_token invalid or expired"
                    })
                    continue

                await process_input(message, websocket, session_id)
    except WebSocketDisconnect:
        logger.info("websocket_text_endpoint disconnected by client")
    except Exception as e:
        logger.error(f"websocket_text_endpoint error: {e}")
        await websocket.close(code=1011)  # 1011: Service restart
    finally:
        logger.info("websocket_text_endpoint connection closing")
        try:
            idle_check_task.cancel()  # Stop the idle check task
            await websocket.close()
        except Exception as e:
            logger.info(f"websocket_text_endpoint connection closing error: {e}")
