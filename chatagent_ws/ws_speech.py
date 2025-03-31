import asyncio
import base64
import json
import logging
import os
from collections import defaultdict
from typing import Optional, Dict, Tuple, Any
from urllib.parse import parse_qs

import nltk
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google.cloud import speech_v1p1beta1 as speech
from google.cloud import texttospeech_v1 as texttospeech
from google.api_core import exceptions as google_exceptions
from httpx import AsyncClient, Timeout
from starlette.websockets import WebSocketState

from chatagent_ws.app_config import APP_API_HOST, APP_API_PORT, APP_SPEECH_GOOGLE_VOICE
from chatagent_ws.session_manager import get_client_ip_from_websocket, validate_token, check_rate_limits
from logging_util import get_logger

load_dotenv()
logger = get_logger("ws_speech_server")
logger.setLevel(logging.DEBUG)  # Ensure debug level is set

APP_USER_SPEECH_LANGUAGE_CODE = os.getenv("APP_USER_SPEECH_LANGUAGE_CODE", "en-US")
APP_BOT_RESPONSE_LANGUAGE_CODE = os.getenv("APP_BOT_RESPONSE_LANGUAGE_CODE", "en-US")

# NLTK Setup
logger.debug("Initializing NLTK punkt tokenizer")
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    logger.debug("Downloading NLTK 'punkt' tokenizer...")
    nltk.download('punkt', quiet=True)
    logger.debug("NLTK 'punkt' downloaded")

# Google Cloud Clients
# speech_client: Optional[speech.SpeechAsyncClient] = None
tts_client: Optional[texttospeech.TextToSpeechClient] = None
google_clients_initialized = False
logger.debug("Attempting to initialize Google Cloud clients")
try:
    # speech_client = speech.SpeechAsyncClient()
    tts_client = texttospeech.TextToSpeechClient()
    google_clients_initialized = True
    logger.info("Google Cloud Speech and TTS Clients initialized")
except Exception as e:
    logger.error(f"FATAL: Failed to initialize Google Cloud Clients: {e}", exc_info=True)

# speech_client=speech.SpeechAsyncClient()

# Constants & State
STT_SILENCE_TIMEOUT_SECONDS = 2.0
stt_streams: Dict[WebSocket, Tuple[None, Optional[asyncio.Task], str, bool]] = {}
stt_request_queues: Dict[WebSocket, asyncio.Queue] = {}
# end_speech_timers: Dict[WebSocket, Optional[asyncio.Task]] = {}
last_transcripts: Dict[WebSocket, str] = defaultdict(str)
bot_speaking_state: Dict[WebSocket, bool] = defaultdict(bool)

AUDIO_ENCODING_MAP = {
    'audio/webm;codecs=opus': speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
    'audio/opus;codecs=opus': speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
    'audio/ogg;codecs=opus': speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
    'audio/wav': speech.RecognitionConfig.AudioEncoding.LINEAR16,
    'audio/l16': speech.RecognitionConfig.AudioEncoding.LINEAR16,
}


async def trigger_speech_end(websocket: WebSocket, session_id: str, voice_name: str):
    logger.debug(f"[{session_id}] Entering trigger_speech_end")
    if websocket not in stt_request_queues:
        logger.warning(f"[{session_id}] trigger_speech_end called for inactive websocket")
        return
    # logger.info(f"[{session_id}] Silence timer expired. Finalizing speech")
    # if websocket in end_speech_timers:
    #     logger.debug(f"[{session_id}] Removing end_speech_timer for websocket")
    #     del end_speech_timers[websocket]
    final_transcript = last_transcripts.pop(websocket, "").strip()
    # logger.info(f"[{session_id}] Final Transcript: '{final_transcript}'")
    if final_transcript:
        # logger.debug(f"[{session_id}] Scheduling process_text_input for transcript: {final_transcript}")
        asyncio.create_task(process_text_input(final_transcript, voice_name, websocket, session_id))
    logger.debug(f"[{session_id}] Exiting trigger_speech_end")


async def handle_stt_responses(stream: Any, websocket: WebSocket, session_id: str, voice_name: str):
    logger.debug(f"[{session_id}] Entering handle_stt_responses")

    # async def timer_wrapper(ws, sid, vname):
    #     logger.debug(f"[{sid}] Starting timer_wrapper")
    #     try:
    #         await asyncio.sleep(STT_SILENCE_TIMEOUT_SECONDS)
    #         if ws.client_state == WebSocketState.CONNECTED and ws in stt_request_queues:
    #             logger.debug(f"[{sid}] Timer expired, triggering speech end")
    #             await trigger_speech_end(ws, sid, vname)
    #     except asyncio.CancelledError:
    #         logger.debug(f"[{sid}] Silence timer cancelled")
    #     logger.debug(f"[{sid}] Exiting timer_wrapper")
    #
    # def start_or_reset_timer():
    #     logger.debug(f"[{session_id}] Starting or resetting timer")
    #     if websocket in end_speech_timers and end_speech_timers[websocket]:
    #         logger.debug(f"[{session_id}] Cancelling existing timer")
    #         end_speech_timers[websocket].cancel()
    #     if websocket in stt_request_queues:
    #         logger.debug(f"[{session_id}] Creating new timer task")
    #         end_speech_timers[websocket] = asyncio.create_task(timer_wrapper(websocket, session_id, voice_name))

    try:
        # logger.debug(f"[{session_id}] Beginning STT response loop")
        async for response in stream:
            logger.debug(f"[{session_id}] Received STT response: {response}")
            if websocket not in stt_request_queues:
                logger.debug(f"[{session_id}] Websocket no longer in queues, breaking loop")
                break

            if response.results:
                logger.debug(f"[{session_id}] Received STT response results: {response.results}")
                for result in response.results:
                    if not result.alternatives:
                        continue # Skip if no alternatives in this result

                    # Get the most likely transcript
                    alternative = result.alternatives[0]
                    transcript = alternative.transcript.strip()

                    if result.is_final:
                        logging.info(f"Store FINAL Transcript: \"{transcript}\" (Confidence: {alternative.confidence:.2f})")
                        last_transcripts[websocket] = transcript
                        await trigger_speech_end(websocket, session_id, voice_name)

            if response.speech_event_type == speech.StreamingRecognizeResponse.SpeechEventType.SPEECH_ACTIVITY_END:
                logger.debug(f"[{session_id}] Speech activity END detected")
            #
            # has_new_speech = response.results and response.results[0].alternatives and response.results[0].alternatives[
            #     0].transcript.strip()
            # if bot_speaking_state.get(websocket, False) and has_new_speech:
            #     logger.debug(f"[{session_id}] User speech detected during bot speech")
            #     bot_speaking_state[websocket] = False
            #     await websocket.send_json({"type": "user_speech_detected", "session_id": session_id})
            # if response.speech_event_type == speech.StreamingRecognizeResponse.SpeechEventType.SPEECH_ACTIVITY_BEGIN or has_new_speech:
            #     logger.debug(f"[{session_id}] Speech activity detected, resetting timer")
            #     start_or_reset_timer()
            # if has_new_speech:
            #     transcript = response.results[0].alternatives[0].transcript
            #     logger.debug(f"[{session_id}] Storing new transcript: {transcript}")
            #     last_transcripts[websocket] = transcript
            # if response.speech_event_type == speech.StreamingRecognizeResponse.SpeechEventType.SPEECH_ACTIVITY_END:
            #     logger.debug(f"[{session_id}] Speech activity END detected")
    except Exception as e:
        logger.error(f"[{session_id}] STT response error: {e}", exc_info=True)
    finally:
        logger.debug(f"[{session_id}] Cleaning up in handle_stt_responses")
        # if websocket in end_speech_timers and end_speech_timers[websocket]:
        #     end_speech_timers[websocket].cancel()
        # if websocket in end_speech_timers:
        #     del end_speech_timers[websocket]
        if websocket in last_transcripts:
            del last_transcripts[websocket]
    logger.debug(f"[{session_id}] Exiting handle_stt_responses")


async def start_google_stt_stream(websocket: WebSocket, session_id: str, audio_format: str, voice_name: str):
    logger.debug(f"[{session_id}] Entering start_google_stt_stream with format: {audio_format}")
    # if not speech_client:
    #     logger.error(f"[{session_id}] Google Speech Client not initialized")
    #     raise ConnectionError("Google Speech Client not initialized")
    if websocket in stt_request_queues:
        logger.debug(f"[{session_id}] STT stream already exists for this websocket")
        return

    encoding = AUDIO_ENCODING_MAP.get(audio_format.lower())
    if not encoding:
        logger.error(f"[{session_id}] Unsupported audio format: {audio_format}")
        raise ValueError(f"Unsupported audio format: {audio_format}")
    sample_rate = 48000
    # sample_rate = 16000 if encoding == speech.RecognitionConfig.AudioEncoding.LINEAR16 else None
    logger.debug(f"[{session_id}] Using encoding: {encoding}, sample_rate: {sample_rate}")

    config = speech.RecognitionConfig(
        encoding=encoding,
        language_code=APP_USER_SPEECH_LANGUAGE_CODE,
        enable_automatic_punctuation=True,
        sample_rate_hertz=sample_rate
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=config,
        interim_results=False,
        single_utterance=False,
        enable_voice_activity_events=True
    )
    logger.debug(f"[{session_id}] STT config created")

    stt_request_queues[websocket] = asyncio.Queue(maxsize=100)
    logger.debug(f"[{session_id}] Created STT request queue")

    async def request_generator():
        # logger.debug(f"[{session_id}] Entering request_generator")
        try:
            # logger.debug(f"[{session_id}] Yielding initial streaming config")
            yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
            while True:
                # logger.debug(f"[{session_id}] Waiting for queue item")
                audio_request = await stt_request_queues[websocket].get()
                if audio_request is None:
                    logger.debug(f"[{session_id}] Received None, stopping generator")
                    break
                # logger.debug(f"[{session_id}] Yielding audio request")
                yield audio_request
                stt_request_queues[websocket].task_done()
        finally:
            logger.debug(f"[{session_id}] Cleaning up request_generator")
            if websocket in stt_request_queues:
                del stt_request_queues[websocket]
        # logger.debug(f"[{session_id}] Exiting request_generator")

    try:
        # logger.debug(f"[{session_id}] Starting streaming_recognize")
        # stream_responses = await speech_client.streaming_recognize(requests=request_generator())
        stream_responses = await speech.SpeechAsyncClient().streaming_recognize(requests=request_generator())
        logger.debug(f"[{session_id}] Creating response handler task")
        response_task = asyncio.create_task(handle_stt_responses(stream_responses, websocket, session_id, voice_name))
        stt_streams[websocket] = (None, response_task, audio_format, False)
        logger.info(f"[{session_id}] Google STT stream started")
    except Exception as e:
        logger.error(f"[{session_id}] Failed to start STT stream: {e}", exc_info=True)
        if websocket in stt_request_queues:
            del stt_request_queues[websocket]
        raise
    logger.debug(f"[{session_id}] Exiting start_google_stt_stream")


async def stop_google_stt_stream(websocket: WebSocket, session_id: Optional[str] = "Unknown"):
    logger.debug(f"[{session_id}] Entering stop_google_stt_stream")
    if websocket in stt_request_queues:
        logger.debug(f"[{session_id}] Signaling queue to stop")
        queue = stt_request_queues.pop(websocket)
        await queue.put(None)
    if websocket in stt_streams:
        # logger.debug(f"[{session_id}] Cancelling response task")
        _, response_task, _, _ = stt_streams.pop(websocket)
        if response_task and not response_task.done():
            response_task.cancel()
            try:
                await asyncio.wait_for(response_task, timeout=1.0)
            except:
                pass
    # if websocket in end_speech_timers:
    #     # logger.debug(f"[{session_id}] Cancelling end_speech_timer")
    #     if end_speech_timers[websocket]:
    #         end_speech_timers[websocket].cancel()
    #     del end_speech_timers[websocket]
    if websocket in last_transcripts:
        # logger.debug(f"[{session_id}] Removing last transcript")
        del last_transcripts[websocket]
    logger.debug(f"[{session_id}] Exiting stop_google_stt_stream")


async def process_audio_chunk(data: dict, websocket: WebSocket, session_id: str):
    # logger.debug(f"[{session_id}] Entering process_audio_chunk with data: {data}")
    if websocket not in stt_request_queues:
        logger.warning(f"[{session_id}] No STT queue for websocket, dropping chunk")
        return
    request_queue = stt_request_queues[websocket]
    try:
        # logger.debug(f"[{session_id}] Decoding base64 audio")
        audio_content = base64.b64decode(data["audio"])
        stt_request = speech.StreamingRecognizeRequest(audio_content=audio_content)
        # logger.debug(f"[{session_id}] Adding audio to queue")
        request_queue.put_nowait(stt_request)
        # logger.debug(f"[{session_id}] Audio chunk queued successfully")
    except asyncio.QueueFull:
        logger.warning(f"[{session_id}] STT queue full. Audio chunk dropped")
    except Exception as e:
        logger.error(f"[{session_id}] Error processing audio chunk: {e}", exc_info=True)
    # logger.debug(f"[{session_id}] Exiting process_audio_chunk")



async def process_text_input(text_input: str, voice_name: str, websocket: WebSocket, session_id: str):
    logger.debug(f"[{session_id}] Entering process_text_input with text: {text_input}")
    if not text_input or not tts_client or websocket.client_state != WebSocketState.CONNECTED:
        logger.debug(
            f"[{session_id}] Invalid input or state, skipping: text={text_input}, tts_client={tts_client}, state={websocket.client_state}")
        return
    bot_speaking_state[websocket] = True
    # logger.debug(f"[{session_id}] Set bot_speaking_state to True")
    timeout = Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
    async_client = AsyncClient(timeout=timeout)
    async with async_client as client:
        try:
            api_endpoint = f"{APP_API_HOST}:{APP_API_PORT}/api/speech/streaming"
            logger.debug(f"[{session_id}] Sending request to API: {api_endpoint}")
            async with client.stream("POST", api_endpoint, json={"message": text_input},
                                     headers={"x-session-id": session_id}) as response:
                response.raise_for_status()
                buffer = ""
                logger.debug(f"[{session_id}] Starting to process API response stream")
                async for chunk in response.aiter_text():
                    logger.debug(f"[{session_id}] Received chunk: {chunk}")
                    if not bot_speaking_state.get(websocket, False):
                        logger.debug(f"[{session_id}] Bot speaking interrupted")
                        break
                    if chunk == "[DONE]":
                        logger.debug(f"[{session_id}] Received [DONE] marker")
                        break
                    buffer += chunk
                    if buffer.strip() and any(p in chunk for p in ".!?"):
                        sentences = nltk.sent_tokenize(buffer)
                        num_to_process = len(sentences) - (1 if not buffer.endswith(('.', '!', '?')) else 0)
                        if num_to_process > 0:
                            text_to_speak = " ".join(sentences[:num_to_process]).strip()
                            buffer = " ".join(sentences[num_to_process:]).strip()
                            if text_to_speak:
                                logger.debug(f"[{session_id}] Processing text to speak: {text_to_speak}")
                                await _process_buffer(text_to_speak, voice_name, websocket, session_id)
                if buffer.strip():
                    logger.debug(f"[{session_id}] Processing final buffer: {buffer}")
                    await _process_buffer(buffer, voice_name, websocket, session_id)
        except Exception as e:
            logger.error(f"[{session_id}] Error in process_text_input: {e}", exc_info=True)
        finally:
            logger.debug(f"[{session_id}] Resetting bot_speaking_state")
            bot_speaking_state[websocket] = False
            await _safe_send(websocket, {"type": "response_end"})
    logger.debug(f"[{session_id}] Exiting process_text_input")


async def _process_buffer(buffer: str, voice_name: str, websocket: WebSocket, session_id: str):
    logger.debug(f"[{session_id}] Entering _process_buffer with buffer: {buffer}")
    if not buffer or not tts_client or websocket.client_state != WebSocketState.CONNECTED:
        logger.debug(
            f"[{session_id}] Skipping buffer processing: buffer={buffer}, tts_client={tts_client}, state={websocket.client_state}")
        return
    try:
        # logger.debug(f"[{session_id}] Synthesizing TTS")
        synthesis_input = texttospeech.SynthesisInput(text=buffer)
        voice = texttospeech.VoiceSelectionParams(language_code=APP_BOT_RESPONSE_LANGUAGE_CODE, name=voice_name)
        audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
        response = await asyncio.get_event_loop().run_in_executor(None,
                                                                  lambda: tts_client.synthesize_speech(
                                                                      input=synthesis_input, voice=voice,
                                                                      audio_config=audio_config))
        audio_content = base64.b64encode(response.audio_content).decode("utf-8")
        logger.debug(f"[{session_id}] Sending TTS audio chunk")
        await _safe_send(websocket, {"type": "audio_chunk", "audio": audio_content, "session_id": session_id})
    except Exception as e:
        logger.error(f"[{session_id}] TTS error: {e}", exc_info=True)
    # logger.debug(f"[{session_id}] Exiting _process_buffer")


async def _safe_send(websocket: WebSocket, data: dict):
    logger.debug(f"Safe send called with data: {data}")
    if websocket.client_state == WebSocketState.CONNECTED:
        logger.debug(f"Sending data to websocket: {data}")
        await websocket.send_json(data)
    else:
        logger.debug(f"Websocket not connected, skipping send: {websocket.client_state}")


async def websocket_speech_endpoint(websocket: WebSocket):
    session_id: Optional[str] = None
    client_ip = get_client_ip_from_websocket(websocket)
    logger.debug(f"WebSocket connection attempt from {client_ip}")

    if not google_clients_initialized:
        logger.error("Google Cloud clients not initialized")
        await websocket.accept()
        await websocket.close(code=1011, reason="Server configuration error")
        return

    try:
        logger.debug(f"Parsing session token from URL: {websocket.url.query}")
        token = parse_qs(websocket.url.query).get("session_token", [None])[0]
        logger.debug(f"Received session token: {token}")
        if not token:
            logger.error("Missing session_token")
            await websocket.close(code=4001, reason="Missing session_token")
            return
        logger.debug(f"Validating token: {token}")
        is_valid, sid = await validate_token(token, client_ip)
        if not is_valid:
            logger.error("Invalid session_token")
            await websocket.close(code=4003, reason="Invalid session_token")
            return
        session_id = sid
        logger.debug(f"Session ID set: {session_id}")
        logger.debug(f"Checking rate limits for {client_ip}")
        is_allowed, reason = await check_rate_limits(client_ip, token)
        if not is_allowed:
            logger.error(f"Rate limit exceeded: {reason}")
            await websocket.close(code=4029, reason=f"Rate limit exceeded: {reason}")
            return

        logger.debug(f"Accepting WebSocket connection")
        await websocket.accept()
        bot_speaking_state[websocket] = False
        default_voice = APP_SPEECH_GOOGLE_VOICE
        logger.debug(f"Sending initial greeting with voice: {default_voice}")
        asyncio.create_task(process_text_input("Hi", default_voice, websocket, session_id))

        logger.debug(f"[{session_id}] Entering main WebSocket loop")
        while True:
            # logger.debug(f"[{session_id}] Waiting for message")
            user_input_raw = await websocket.receive_text()
            # logger.debug(f"[{session_id}] Received raw input: {user_input_raw}")
            try:
                data = json.loads(user_input_raw)
                msg_type = data.get("type")
                # logger.debug(f"[{session_id}] Message type: {msg_type}")

                if msg_type == "audio_input_chunk":
                    # logger.debug(f"[{session_id}] Processing audio_input_chunk: {data}")
                    audio_format = data.get("format")
                    if not audio_format:
                        logger.error(f"[{session_id}] Missing audio format")
                        await _safe_send(websocket, {"type": "stream_error", "text": "Missing audio format"})
                        continue
                    if websocket not in stt_request_queues:
                        logger.debug(f"[{session_id}] Starting STT stream for format: {audio_format}")
                        await start_google_stt_stream(websocket, session_id, audio_format, default_voice)
                    # logger.debug(f"[{session_id}] Processing audio chunk")
                    await process_audio_chunk(data, websocket, session_id)

                elif msg_type == "userInput":
                    logger.debug(f"[{session_id}] Processing userInput")
                    text = data.get('text', '').strip()
                    if text:
                        logger.debug(f"[{session_id}] Received text input: {text}")
                        # if websocket in end_speech_timers and end_speech_timers[websocket]:
                        #     logger.debug(f"[{session_id}] Cancelling existing timer")
                        #     end_speech_timers[websocket].cancel()
                        #     end_speech_timers[websocket] = None
                        logger.debug(f"[{session_id}] Scheduling text input processing")
                        asyncio.create_task(process_text_input(text, default_voice, websocket, session_id))

                else:
                    logger.warning(f"[{session_id}] Unknown message type: {msg_type}")

            except json.JSONDecodeError:
                logger.error(f"[{session_id}] Invalid JSON received: {user_input_raw}")
            except Exception as e:
                logger.error(f"[{session_id}] Error processing message: {e}", exc_info=True)

    except WebSocketDisconnect:
        logger.info(f"[{session_id or 'Unknown'}] WebSocket disconnected by client {client_ip}")
    except Exception as e:
        logger.error(f"[{session_id or 'Unknown'}] Unhandled WebSocket error: {e}", exc_info=True)
    finally:
        logger.debug(f"[{session_id or 'Unknown'}] Cleaning up WebSocket connection")
        await stop_google_stt_stream(websocket, session_id)
        if websocket in bot_speaking_state:
            del bot_speaking_state[websocket]
        if websocket.client_state != WebSocketState.DISCONNECTED:
            logger.debug(f"[{session_id or 'Unknown'}] Closing WebSocket")
            await websocket.close()
        logger.info(f"[{session_id or 'Unknown'}] Connection closed for {client_ip}")
