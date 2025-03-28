import asyncio
import base64
import json
import logging
from typing import AsyncIterator, Optional, Dict
from urllib.parse import parse_qs

import nltk
from dotenv import load_dotenv
from fastapi import WebSocket, WebSocketDisconnect
from google.cloud import texttospeech
from httpx import AsyncClient, TimeoutException, RequestError, HTTPStatusError
from nltk.tokenize import sent_tokenize

from chatagent_ws.AppConfig import APP_API_HOST, APP_API_PORT, APP_WS_IDLE_TIMEOUT_SECONDS, APP_SPEECH_GOOGLE_VOICE
from chatagent_ws.LoggingUtil import get_logger
from chatagent_ws.session_manager import validate_token, check_rate_limits

load_dotenv()

# NLTK setup
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

logger = get_logger("ws_speech")
logging.basicConfig(level=logging.INFO)

client = texttospeech.TextToSpeechClient()

MAX_BUFFER_SIZE = 1024 * 1024  # 1MB buffer limit
MAX_INPUT_SIZE = 10 * 1024  # 10KB input limit


async def call_speech_streaming_api(
        message: str,
        x_session_id: str,
        base_url: str = f"{APP_API_HOST}:{APP_API_PORT}",
        timeout: float = 60.0,
        headers: Optional[Dict[str, str]] = None,
) -> AsyncIterator[str]:
    logger.info("Calling speech streaming API")
    url = f"{base_url}/api/speech/streaming"
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
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    yield chunk
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
    voice_name = data.get("voice", None)

    # Validate voice_name
    if not voice_name or not all(c.isalnum() or c in "-_" for c in voice_name):
        voice_name = APP_SPEECH_GOOGLE_VOICE

    if not text_input:
        await websocket.send_json({
            "type": "stream_error",
            "text": "Empty input received"
        })
        return

    try:
        buffer = ""
        async for chunk in call_speech_streaming_api(text_input, session_id):
            if len(buffer) + len(chunk) > MAX_BUFFER_SIZE:
                logger.error("Buffer size exceeded")
                await websocket.send_json({
                    "type": "stream_error",
                    "text": "Response too large"
                })
                return

            if chunk == "[DONE]":
                if buffer.strip():
                    await _process_buffer(buffer.strip(), voice_name, websocket, session_id)
                break
            elif "Error:" in chunk:
                await websocket.send_json({"type": "stream_error", "text": chunk})
                return
            else:
                buffer += chunk
                sentences = sent_tokenize(buffer)
                if len(sentences) > 1 or (sentences and chunk.endswith(('. ', '? ', '! '))):
                    await _process_buffer(sentences[0].strip(), voice_name, websocket, session_id)
                    buffer = sentences[-1]

        await websocket.send_json({"type": "response_end"})
    except Exception as e:
        logger.error(f"Processing error: {e}")
        await websocket.send_json({"type": "stream_error", "text": str(e)})


async def _process_buffer(text: str, voice_name: str, websocket: WebSocket, session_id: str):
    if text:
        await send_text_and_audio(text, voice_name, websocket, session_id)


async def send_text_and_audio(text: str, voice_name: str, websocket: WebSocket, session_id: str):
    try:
        await websocket.send_json({
            "type": "response_chunk",
            "text": text,
            "session_id": session_id
        })

        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code=f"{voice_name.split('-')[0]}-{voice_name.split('-')[1]}",
            name=voice_name
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.0,
            pitch=0.0
        )

        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        audio_data = response.audio_content
        base64_audio = base64.b64encode(audio_data).decode('utf-8')

        await websocket.send_json({
            "type": "audio_chunk",
            "audio": base64_audio,
            "session_id": session_id
        })
    except Exception as e:
        logger.error(f"Send text/audio error: {e}")
        raise


async def websocket_speech_endpoint(websocket: WebSocket):
    """
    Handles a WebSocket connection for speech input, including a connection
    idle check.  If the client is idle for more than 10 minutes, the
    connection is closed.

    Args:
        websocket: The WebSocket connection object.
    """
    await websocket.accept()
    logger.info("websocket_speech_endpoint connection established")

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
                logger.info("Connection idle timeout exceeded. Closing connection.")
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

        client_ip = websocket.client.host if websocket.client else "unknown"
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
            last_activity = asyncio.get_event_loop().time() #update last_activity
            data = json.loads(message)
            if data.get("type") == "userInput":
                session_token = data.get("session_token")
                if not session_token:
                    await websocket.send_json({
                        "type": "stream_error",
                        "text": "Missing session_token"
                    })
                    continue
                client_ip = websocket.client.host if websocket.client else "unknown"
                is_valid, session_id = await validate_token(session_token, client_ip)
                if not is_valid:
                    await websocket.send_json({
                        "type": "stream_error",
                        "text": "Session_token invalid or expired"
                    })
                    continue
                is_in_ratelimit, result = await check_rate_limits(client_ip, session_token)
                if not is_in_ratelimit:
                    await websocket.send_json({
                        "type": "stream_error",
                        "text": f"{result}"
                    })
                    continue
                await process_input(message, websocket, session_id)
    except WebSocketDisconnect:
        logger.info("websocket_speech_endpoint disconnected by client")
    except Exception as e:
        logger.error(f"websocket_speech_endpoint error: {e}")
        await websocket.close(code=1011)  # 1011: Service restart
    finally:
        logger.info("websocket_speech_endpoint connection closing")
        try:
            idle_check_task.cancel()  # Stop the idle check task
            await websocket.close()
        except Exception as e:
            logger.info(f"websocket_speech_endpoint connection closing error: {e}")
