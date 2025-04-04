import asyncio
import json
from typing import AsyncIterator, Optional, Dict
from urllib.parse import parse_qs

from dotenv import load_dotenv
from fastapi import WebSocket, WebSocketDisconnect
from google.cloud import texttospeech
from httpx import AsyncClient, TimeoutException, RequestError, HTTPStatusError

from app_config import APP_API_HOST, APP_API_PORT, APP_WS_IDLE_TIMEOUT_SECONDS
from language_util import detect_language_code_and_voice_name, spacy_tokenize_text, extract_language_name_from_llm_text, \
    get_voice_code_name_by_language_name, fix_markdown_list_spacing
from logging_util import get_logger
from session_manager import validate_token, check_rate_limits, get_client_ip_from_websocket

load_dotenv()

logger = get_logger("ws_speech")

text_to_speech_client = texttospeech.TextToSpeechClient()

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

    if not text_input:
        await websocket.send_json({
            "type": "stream_error",
            "text": "Empty input received"
        })
        return

    try:
        buffer = ""
        language_name = "ENGLISH"
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
                    lang_code, voice_code, voice_name = get_voice_code_name_by_language_name(language_name)
                    # lang_code, voice_code, voice_name, lang_name = detect_language_code_and_voice_name(buffer.strip())
                    await send_text_and_audio(buffer.strip(), websocket, lang_code, voice_code, voice_name)
                break
            elif "Error:" in chunk:
                await websocket.send_json({"type": "stream_error", "text": chunk})
                return
            else:
                logger.debug(f"received llm chunk:{chunk}")
                cleaned_chunk = (chunk.replace("** ", "").replace("-- ", "")
                                 .replace("* ", ""))
                buffer += cleaned_chunk
                llm_language_name=extract_language_name_from_llm_text(buffer.strip())
                if llm_language_name is not None:
                    buffer=buffer.strip().replace(f"language-name:{llm_language_name}","").replace("\n","")
                    language_name=llm_language_name.upper()
                    # logger.info(f"language name: {llm_language_name}")
                # lang_code, voice_code, voice_name, lang_name = detect_language_code_and_voice_name(buffer.strip())
                lang_code, voice_code, voice_name = get_voice_code_name_by_language_name(language_name)
                logger.info(f"detected language: {language_name} {lang_code}, {voice_name}")
                sentences=spacy_tokenize_text(buffer,language_name)
                logger.debug(f"sentences list:{sentences}")
                if len(sentences) > 1 or (sentences and cleaned_chunk.endswith(('. ', '? ', '! '))):
                    if sentences[0].strip():
                        await send_text_and_audio(sentences[0].strip(), websocket, lang_code, voice_code, voice_name)
                    else:
                        logger.debug(f"skip empty sentence:{sentences[0]}")
                    buffer = sentences[-1]

        await websocket.send_json({"type": "response_end"})
    except Exception as e:
        logger.error(f"Processing error: {e}")
        logger.exception(e)
        await websocket.send_json({"type": "stream_error", "text": str(e)})


async def send_text_and_audio(text: str, websocket: WebSocket, lang_code: str, voice_code: str, voice_name: str):
    try:
        # lang_code, voice_code, voice_name, lang_name = detect_language_code_and_voice_name(text.strip())
        # logger.debug(f"detected language code: {lang_code}, {voice_name}")
        logger.debug(f"send_text_and_audio: {text}")
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code=f"{voice_code}",
            name=voice_name
        )
        audio_config = texttospeech.AudioConfig(
            # audio_encoding=texttospeech.AudioEncoding.LINEAR16,  # Uncompressed 16-bit PCM
            # sample_rate_hertz=24000,
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.0,
            pitch=0.0
        )

        response = text_to_speech_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )
        audio_data = response.audio_content
        # base64_audio = base64.b64encode(audio_data).decode('utf-8')
        # await websocket.send_json({
        #     "type": "audio_chunk",
        #     "audio": base64_audio,
        #     "voice_name": voice_name
        # })
        logger.debug(f"before send audio")
        await websocket.send_bytes(audio_data)
        logger.debug(f"before send metadata")
        metadata = {"type": "audio_metadata", "format": "mp3", "lang_code": lang_code,
                    "length": len(response.audio_content)}
        await websocket.send_json(metadata)
        logger.debug(f"before send text")
        await websocket.send_json({
            "type": "response_chunk",
            "text": text
        })

    except Exception as e:
        logger.exception(f"Send text/audio error: {e}")
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
    idle_timeout = APP_WS_IDLE_TIMEOUT_SECONDS

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
            if data.get("type") == "userInput" or data.get("type") == "user_input":
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
