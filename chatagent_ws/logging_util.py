import logging
import os
from logging.handlers import RotatingFileHandler

from app_config import APP_LOG_LEVEL, APP_LOG_FILE_ENABLED, APP_LOG_FILE_PATH


def get_logger(name):
    logger = logging.getLogger(name)
    if not logger.hasHandlers():
        logger.setLevel(APP_LOG_LEVEL)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        if APP_LOG_FILE_ENABLED:
            try:
                if not os.path.isdir(APP_LOG_FILE_PATH):
                    os.makedirs(APP_LOG_FILE_PATH, exist_ok=True)
                file_handler = RotatingFileHandler(
                    f"{APP_LOG_FILE_PATH}/chatagent-ws.log",
                    maxBytes=10485760,
                    backupCount=5,
                    encoding='utf-8'  # Add encoding for file handler
                )
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
            except Exception as e:
                print(f"Error to create file logger: {e}")

    return logger