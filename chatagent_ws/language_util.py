# import nltk
from dotenv import load_dotenv
from lingua import LanguageDetectorBuilder, Language
import re

from app_config import APP_SPEECH_GOOGLE_VOICE_CN, APP_SPEECH_GOOGLE_VOICE_ES, APP_SPEECH_GOOGLE_VOICE_FR, \
    APP_SPEECH_GOOGLE_VOICE_DE, APP_SPEECH_GOOGLE_VOICE_JP, APP_SPEECH_GOOGLE_VOICE_KR, \
    APP_SPEECH_GOOGLE_VOICE_EN, APP_SPEECH_GOOGLE_VOICE_RU
from logging_util import get_logger
import spacy
from spacy.cli import download

load_dotenv()

logger = get_logger("language_util")

# lingua code
lang_code_en = "en-US"
lang_code_fr = "fr-FR"
lang_code_de = "de-DE"
lang_code_es = "es-ES"
lang_code_in = "hi-IN"
lang_code_jp = "jp-JP"
lang_code_kr = "ko-KR"
lang_code_cn = "zh-CN"
lang_code_it = "it-IT"
lang_code_pt = "pt-PT"
lang_code_ir = "fa-IR"
lang_code_ru = "ru-RU"
# tts api code
voice_code_cn = "cmn-CN"

lingua_languages = [Language.ENGLISH, Language.FRENCH, Language.GERMAN, Language.SPANISH,
                    Language.CHINESE, Language.KOREAN, Language.JAPANESE
                    ]
lingua_detector = (LanguageDetectorBuilder.from_languages(*lingua_languages)
                   .with_preloaded_language_models().build())
logger.info(f"lingua language detector initiated successfully")

# NLTK setup
# try:
#     nltk.data.find('tokenizers/punkt')
# except LookupError:
#     nltk.download('punkt')
#     nltk.download('punkt_tab')
#
# logger.info(f"nltk tokenizers initiated successfully")

spacy_models_names = {
    Language.ENGLISH.name: "en_core_web_sm",
    Language.FRENCH.name: "fr_core_news_sm",
    Language.SPANISH.name: "es_core_news_sm",
    Language.GERMAN.name: "de_core_news_sm",
    Language.CHINESE.name: "zh_core_web_sm",
    Language.JAPANESE.name: "ja_core_news_sm",
    Language.KOREAN.name: "ko_core_news_sm"
}

def load_spacy_model(model_name):
    """
    Loads a spaCy language model. Downloads it at runtime if not found.
    Args:
        model_name (str): The name of the spaCy model to load (e.g., "en_core_web_sm").
    Returns:
        spacy.language.Language or None: The loaded spaCy model, or None if download fails.
    """
    try:
        nlp = spacy.load(model_name)
        logger.info(f"Successfully loaded model: {model_name}")
        return nlp
    except OSError:
        logger.info(f"Model '{model_name}' not found. Downloading...")
        try:
            download(model_name)
            nlp = spacy.load(model_name)
            logger.info(f"Successfully downloaded and loaded model: {model_name}")
            return nlp
        except Exception as e:
            logger.error(f"Error downloading model '{model_name}': {e}")
            return None


def load_spacy_models():
    models={}
    for lang_name, model_name in spacy_models_names.items():
        try:
            models[lang_name] = load_spacy_model(model_name)
            logger.info(f"Loaded model for {lang_name}")
        except OSError:
            logger.error(f"Error: Could not load model '{lang_name}). "
                  f"Please run: python -m spacy download {model_name}")
            models[lang_name] = None
    return models

spacy_models = load_spacy_models()


def spacy_tokenize_text(text:str, lang_name:str):
    nlp=spacy_models[lang_name]
    doc = nlp(text)
    return [sent.text for sent in doc.sents]


def detect_language_code_and_voice_name(text: str):
    result_name = "ENGLISH"
    try:
        # for result in detector.detect_multiple_languages_of(text):
        #     # pick up first non english
        #     logger.debug(f"detected multi language: {result_name} on: {text}")
        #     if result.language.name != "ENGLISH":
        #         result_name = result.language.name
        result_name=lingua_detector.detect_language_of(text).name
        logger.debug(f"detected final language: {result_name} : {text}")

        if result_name == "ENGLISH":
            return lang_code_en, lang_code_en, APP_SPEECH_GOOGLE_VOICE_EN, result_name
        elif result_name == "FRENCH":
            return lang_code_fr, lang_code_fr, APP_SPEECH_GOOGLE_VOICE_FR, result_name
        elif result_name == "SPANISH":
            return lang_code_es, lang_code_es, APP_SPEECH_GOOGLE_VOICE_ES, result_name
        # elif result_name == "HINDI":
        #     return lang_code_in, lang_code_in, APP_SPEECH_GOOGLE_VOICE_IN, result_name
        elif result_name == "GERMAN":
            return lang_code_de, lang_code_de, APP_SPEECH_GOOGLE_VOICE_DE, result_name
        elif result_name == "CHINESE":
            return lang_code_cn, voice_code_cn, APP_SPEECH_GOOGLE_VOICE_CN, result_name
        elif result_name == "JAPANESE":
            return lang_code_jp, lang_code_jp, APP_SPEECH_GOOGLE_VOICE_JP, result_name
        elif result_name == "KOREAN":
            return lang_code_kr, lang_code_kr, APP_SPEECH_GOOGLE_VOICE_KR, result_name
        elif result_name == "RUSSIAN":
            return lang_code_ru, lang_code_ru, APP_SPEECH_GOOGLE_VOICE_RU, result_name
        # default
        else:
            return lang_code_en, lang_code_en, APP_SPEECH_GOOGLE_VOICE_EN, result_name
    except Exception as e:
        return lang_code_cn, lang_code_en, APP_SPEECH_GOOGLE_VOICE_EN, result_name

def extract_language_name_from_llm_text(text:str):
    pattern = r"language-name:([a-zA-Z]+)(?:\s+|\n|$)"
    match = re.search(pattern, text)
    if match:
        return match.group(1)
    else:
        return None

def get_voice_code_name_by_language_name(lang_name:str):
    if lang_name is None or lang_name == "ENGLISH":
        return lang_code_en, lang_code_en, APP_SPEECH_GOOGLE_VOICE_EN
    elif lang_name == "FRENCH":
        return lang_code_fr, lang_code_fr, APP_SPEECH_GOOGLE_VOICE_FR
    elif lang_name == "SPANISH":
        return lang_code_es, lang_code_es, APP_SPEECH_GOOGLE_VOICE_ES
    # elif lang_name == "HINDI":
    #     return lang_code_in, lang_code_in, APP_SPEECH_GOOGLE_VOICE_IN
    elif lang_name == "GERMAN":
        return lang_code_de, lang_code_de, APP_SPEECH_GOOGLE_VOICE_DE
    elif lang_name == "CHINESE":
        return lang_code_cn, voice_code_cn, APP_SPEECH_GOOGLE_VOICE_CN
    elif lang_name == "JAPANESE":
        return lang_code_jp, lang_code_jp, APP_SPEECH_GOOGLE_VOICE_JP
    elif lang_name == "KOREAN":
        return lang_code_kr, lang_code_kr, APP_SPEECH_GOOGLE_VOICE_KR
    elif lang_name == "RUSSIAN":
        return lang_code_ru, lang_code_ru, APP_SPEECH_GOOGLE_VOICE_RU
    # default
    else:
        return lang_code_en, lang_code_en, APP_SPEECH_GOOGLE_VOICE_EN

