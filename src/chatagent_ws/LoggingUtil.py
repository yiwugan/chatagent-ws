import logging
from logging.handlers import RotatingFileHandler

from .AppConfig import APP_LOG_LEVEL

def get_logger(name):
    logger = logging.getLogger(name)
    if not logger.hasHandlers():
        logger.setLevel(APP_LOG_LEVEL)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        file_handler = RotatingFileHandler('app.log', maxBytes=10485760, backupCount=5)
        file_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
    return logger

