# simple_stt_stream_test.py
import asyncio
import logging

# from dotenv import load_dotenv
from google.cloud import speech_v1p1beta1 as speech
from google.api_core import exceptions as google_exceptions

# load_dotenv()


# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

async def test_stt_streaming_connection():
    """
    Tests basic connectivity and authentication for Google STT streaming API.
    Focuses on establishing the stream without sending audio.
    """
    logging.info("--- Test Start ---")
    client = None # Initialize client to None for finally block
    stream_responses = None # Initialize responses to None

    # 1. Initialize Client
    logging.info("Attempting to initialize Google Speech Async Client...")
    try:
        # This step checks if credentials can be found and are valid format
        client = speech.SpeechAsyncClient()
        logging.info("Speech client initialized successfully.")
    except Exception as e:
        logging.error(f"Failed to initialize speech client: {e}", exc_info=True)
        logging.error("Check your authentication setup:")
        logging.error(" - Is GOOGLE_APPLICATION_CREDENTIALS environment variable set correctly?")
        logging.error(" - Or, if using Application Default Credentials (ADC), run `gcloud auth application-default login`?")
        logging.error(" - Ensure the credential file is valid and readable.")
        return # Cannot proceed without a client

    # 2. Define Minimal Configuration
    try:
        # Using placeholder config values sufficient for stream setup test
        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16, # Required, but value less important for connection test
            sample_rate_hertz=16000, # Required, but value less important for connection test
            language_code="en-US",   # Required, use a valid language code
            enable_automatic_punctuation=False, # Keep config simple
        )
        # Use streaming settings relevant to the main code's issue
        streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=True,       # Test with interim results enabled
            single_utterance=False      # Test continuous streaming mode setup
        )
        logging.info(f"Using StreamingRecognitionConfig: interim={streaming_config.interim_results}, single_utterance={streaming_config.single_utterance}")
    except Exception as e:
        logging.error(f"Failed to create configuration objects: {e}", exc_info=True)
        return

    # 3. Define Minimal Request Generator (Only Config)
    async def test_generator():
        try:
            logging.info("Test generator: Yielding StreamingRecognitionConfig...")
            yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
            logging.info("Test generator: Finished yielding config (no audio will be sent).")
            # For this test, we stop immediately after sending the config.
            # This isolates the initial stream setup phase.
        except Exception as e:
            logging.error(f"Error within test_generator: {e}", exc_info=True)
            raise # Re-raise to be caught by the main handler

    # 4. Call the API and Handle Responses/Errors
    try:
        logging.info("Calling client.streaming_recognize()...")
        # This is the critical point where the hang occurs in the main application.
        stream_responses = await client.streaming_recognize(requests=test_generator())
        # If the code reaches here, the await didn't hang indefinitely.
        logging.info("PASS: client.streaming_recognize() call returned successfully (didn't hang).")

        # 5. Attempt to Iterate the Stream Briefly
        logging.info("Attempting to iterate the response stream to confirm handshake...")
        # We expect this to potentially time out or yield nothing quickly,
        # as we sent no audio and closed the generator.
        # The goal is just to see if iterating *starts* without error.
        first_response = None
        try:
            # Use anext() to get just the first item or timeout/stop iteration
            first_response = await asyncio.wait_for(anext(stream_responses), timeout=5.0)
            logging.info(f"PASS: Successfully received first item from stream: {type(first_response)}")
            if first_response and first_response.error.message:
                 logging.warning(f"Stream established but received error in first response: {first_response.error.message}")
            else:
                 logging.info("Initial response received without immediate error.")

        except StopAsyncIteration:
            logging.info("PASS: Response stream ended immediately (as expected, no audio sent). Handshake likely OK.")
        except asyncio.TimeoutError:
            logging.info("PASS: Timed out waiting for first response (likely OK, means stream stayed open waiting for audio). Handshake likely OK.")
        except Exception as e_iter:
            # Catch errors during iteration itself
             logging.error(f"FAIL: Error occurred while attempting to iterate the stream: {e_iter}", exc_info=True)
             logging.error("This might indicate an issue after the initial connection was made.")
             return # Exit after iteration failure

        # If we got past the iteration attempt without critical errors:
        logging.info("--- TEST RESULT: BASIC CONNECTION AND AUTH SEEM OK ---")
        logging.info("The streaming_recognize call did not hang and the initial stream handshake appears successful.")

    # --- Specific Google API Error Handling ---
    except google_exceptions.PermissionDenied as e:
        logging.error(f"FAIL: Permission Denied (403): {e}", exc_info=False) # Less detail needed for known error
        logging.error("ACTION: Check IAM roles. Ensure the service account has 'roles/speech.recognizer'.")
    except google_exceptions.Unauthenticated as e:
        logging.error(f"FAIL: Unauthenticated (401): {e}", exc_info=False)
        logging.error("ACTION: Check credentials (ADC / GOOGLE_APPLICATION_CREDENTIALS).")
    except google_exceptions.FailedPrecondition as e:
        # This often means the API is not enabled
        logging.error(f"FAIL: Failed Precondition (400/412): {e}", exc_info=False)
        logging.error("ACTION: Check API Enablement. Ensure 'Cloud Speech-to-Text API' is ENABLED in the Cloud Console for your project.")
    except google_exceptions.InvalidArgument as e:
        logging.error(f"FAIL: Invalid Argument (400): {e}", exc_info=False)
        logging.error("ACTION: Check the RecognitionConfig/StreamingRecognitionConfig parameters in this script (unlikely with this minimal config).")
    except google_exceptions.GoogleAPICallError as e:
        # Catch other potential Google API errors (network, quota, internal, etc.)
        logging.error(f"FAIL: Google API Call Error: {e}", exc_info=True)
        logging.error("ACTION: Check network connectivity (firewalls, proxies), STT quotas, or temporary Google Cloud status.")
    except asyncio.CancelledError:
        logging.warning("Operation cancelled.")
    except Exception as e:
        # Catch any other unexpected errors
        logging.error(f"FAIL: An unexpected error occurred: {e}", exc_info=True)
        logging.error("ACTION: This could be a client library bug, unexpected generator issue, or other system problem.")

    finally:
        # Note: Explicitly closing the gRPC stream can be complex.
        # In this simple test, letting the client/stream go out of scope is usually sufficient.
        # If the client has a close method (check documentation if needed):
        # if client and hasattr(client, "close"):
        #    try:
        #        await client.close()
        #        logging.info("Speech client closed.")
        #    except Exception as e_close:
        #        logging.warning(f"Error closing speech client: {e_close}")
        logging.info("--- Test End ---")


if __name__ == "__main__":
    logging.info("Starting simple STT streaming connection test script...")
    # Ensure authentication is set up correctly in your environment
    # (e.g., `gcloud auth application-default login` or setting GOOGLE_APPLICATION_CREDENTIALS)
    asyncio.run(test_stt_streaming_connection())