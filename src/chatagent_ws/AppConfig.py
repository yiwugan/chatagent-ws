# config.py
import os

APP_NAME = os.getenv("APP_NAME", "chatagent_ws")
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")

APP_WS_PORT= os.getenv("APP_WS_PORT", 8001)
APP_WS_API_KEY=os.getenv("APP_WS_API_KEY", "")

APP_API_HOST= os.getenv("APP_API_HOST", "localhost")
APP_API_PORT= os.getenv("APP_API_PORT", 8002)
APP_API_KEY=os.getenv("APP_API_KEY", "")

# dev pr production
APP_ENV = os.getenv("APP_ENV", "dev")
APP_LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "INFO")
APP_LOG_FILE_PATH = os.getenv("APP_LOG_FILE_PATH", "./logs")
APP_LOG_FILE_ENABLED = os.getenv("APP_LOG_FILE_ENABLED", True)

# web socket security
APP_CONNECTION_MAX_SESSIONS_PER_IP=os.getenv("APP_CONNECTION_MAX_SESSIONS_PER_IP", 5)
APP_CONNECTION_MAX_REQUESTS_PER_MINUTE=os.getenv("APP_CONNECTION_MAX_REQUESTS_PER_MINUTE", 10)
APP_SECURITY_TOKEN_EXPIRY_SECONDS=os.getenv("APP_SECURITY_TOKEN_EXPIRY_SECONDS", 30)
