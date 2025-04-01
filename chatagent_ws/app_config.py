# config.py
import os

from dotenv import load_dotenv

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "chatagent_ws")
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")

# APP_WS_HOST= os.getenv("APP_WS_HOST", "0.0.0.0")
APP_WS_PORT = int(os.getenv("APP_WS_PORT", 8001))
APP_WS_API_KEY = os.getenv("APP_WS_API_KEY", "")
APP_WS_TIMEOUT_SECONDS = int(os.getenv("APP_WS_TIMEOUT_SECONDS", 600))

APP_WS_ALLOWED_ORIGIN = os.getenv("APP_WS_ALLOWED_ORIGIN", "https://localhost")

APP_API_HOST = os.getenv("APP_API_HOST", "http://localhost")
APP_API_PORT = int(os.getenv("APP_API_PORT", 8002))
APP_API_KEY = os.getenv("APP_API_KEY", "")

# dev pr production
APP_ENV = os.getenv("APP_ENV", "dev")
APP_LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "DEBUG")
APP_LOG_FILE_PATH = os.getenv("APP_LOG_FILE_PATH", "./logs")
APP_LOG_FILE_ENABLED = bool(os.getenv("APP_LOG_FILE_ENABLED", True))

# web socket security
APP_CONNECTION_MAX_SESSIONS_PER_IP = int(os.getenv("APP_CONNECTION_MAX_SESSIONS_PER_IP", 20))
APP_CONNECTION_MAX_REQUESTS_PER_MINUTE = int(os.getenv("APP_CONNECTION_MAX_REQUESTS_PER_MINUTE", 30))
APP_SECURITY_TOKEN_EXPIRY_SECONDS = int(os.getenv("APP_SECURITY_TOKEN_EXPIRY_SECONDS", 900))

APP_REDIS_HOST = os.getenv("APP_REDIS_HOST", "localhost")
APP_REDIS_PORT = int(os.getenv("APP_REDIS_PORT", 6379))
APP_REDIS_DB = int(os.getenv("APP_REDIS_DB", 0))
APP_REDIS_PASSWORD = os.getenv("APP_REDIS_PASSWORD", None)

APP_WS_IDLE_TIMEOUT_SECONDS = int(os.getenv("APP_WS_IDLE_TIMEOUT_SECONDS", 600))

APP_SPEECH_GOOGLE_VOICE = os.getenv("APP_SPEECH_GOOGLE_VOICE", "en-US-Standard-A")

APP_SPEECH_GOOGLE_VOICE_EN = os.getenv("APP_SPEECH_GOOGLE_VOICE_EN", "en-US-Wavenet-C")
APP_SPEECH_GOOGLE_VOICE_FR = os.getenv("APP_SPEECH_GOOGLE_VOICE_FR", "fr_CA-Wavenet-A")
APP_SPEECH_GOOGLE_VOICE_JP = os.getenv("APP_SPEECH_GOOGLE_VOICE_JP", "ja-JP-Wavenet-A")
APP_SPEECH_GOOGLE_VOICE_KR = os.getenv("APP_SPEECH_GOOGLE_VOICE_KR", "ko-KR-Wavenet-A")
APP_SPEECH_GOOGLE_VOICE_CN = os.getenv("APP_SPEECH_GOOGLE_VOICE_CN", "cmn-CN-Wavenet-A")
APP_SPEECH_GOOGLE_VOICE_ES = os.getenv("APP_SPEECH_GOOGLE_VOICE_ES", "es-ES-Wavenet-C")
APP_SPEECH_GOOGLE_VOICE_DE = os.getenv("APP_SPEECH_GOOGLE_VOICE_DE", "de-DE-Wavenet-A")
APP_SPEECH_GOOGLE_VOICE_IN = os.getenv("APP_SPEECH_GOOGLE_VOICE_IN", "hi-IN-Wavenet-A")




#en-US-Standard-A
#en-US-Chirp3-HD-Aoede