import asyncio
import logging
import os
from dotenv import load_dotenv
from google.cloud import speech_v1p1beta1 as speech
from google.api_core import exceptions as google_exceptions

load_dotenv()

# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


async def do_test_stt_streaming_connection():
    """
    Tests basic connectivity and authentication for Google STT streaming API.
    Focuses on establishing the stream without sending audio.
    """
    logging.info("--- Test Start ---")
    client = None

    # 1. Initialize Client with explicit credential check
    logging.info("Attempting to initialize Google Speech Async Client...")
    try:
        # Check if credentials are available before creating client
        if not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
            logging.warning("GOOGLE_APPLICATION_CREDENTIALS not set in environment")
        client = speech.SpeechAsyncClient()
        logging.info("Speech client initialized successfully.")
    except Exception as e:
        logging.error(f"Failed to initialize speech client: {e}", exc_info=True)
        logging.error("Check your authentication setup:")
        logging.error(" - GOOGLE_APPLICATION_CREDENTIALS should point to your service account key file")
        logging.error(" - Or run `gcloud auth application-default login` for ADC")
        return

    # 2. Define Configuration
    try:
        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="en-US",
            enable_automatic_punctuation=False,
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=True,
            single_utterance=False
        )
        logging.info(
            f"Using StreamingRecognitionConfig: interim={streaming_config.interim_results}, single_utterance={streaming_config.single_utterance}")
    except Exception as e:
        logging.error(f"Failed to create configuration objects: {e}", exc_info=True)
        return

    # 3. Simplified Async Generator
    async def request_generator():
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
        logging.info("Test generator: Config yielded (no audio sent)")

    # 4. Test the Streaming Connection
    try:
        logging.info("Calling client.streaming_recognize()...")
        # stream_responses = await client.streaming_recognize(requests=request_generator())
        stream_responses = await speech.SpeechAsyncClient().streaming_recognize(requests=request_generator())

        logging.info("PASS: streaming_recognize() call returned successfully")

        # Test stream iteration
        async for response in stream_responses:
            logging.info(f"Received response: {type(response)}")
            if hasattr(response, 'error') and response.error.message:
                logging.warning(f"Stream error: {response.error.message}")
            break  # We only need the first response for testing

        logging.info("--- TEST RESULT: BASIC CONNECTION AND AUTH SEEM OK ---")

    except google_exceptions.PermissionDenied as e:
        logging.error(f"FAIL: Permission Denied (403): {e}")
        logging.error("ACTION: Ensure service account has 'roles/speech.recognizer'")
    except google_exceptions.Unauthenticated as e:
        logging.error(f"FAIL: Unauthenticated (401): {e}")
        logging.error("ACTION: Verify credentials")
    except google_exceptions.FailedPrecondition as e:
        logging.error(f"FAIL: Failed Precondition (400/412): {e}")
        logging.error("ACTION: Enable 'Cloud Speech-to-Text API' in Cloud Console")
    except Exception as e:
        logging.error(f"FAIL: Unexpected error: {e}", exc_info=True)

    finally:
        # Proper cleanup
        if client and hasattr(client, 'transport'):
            try:
                await client.transport.close()
                logging.info("Client transport closed")
            except Exception as e:
                logging.warning(f"Error closing client transport: {e}")
        logging.info("--- Test End ---")


if __name__ == "__main__":
    logging.info("Starting STT streaming connection test...")
    asyncio.run(do_test_stt_streaming_connection())