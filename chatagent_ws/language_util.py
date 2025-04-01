import pycld2 as cld2
import time

from dotenv import load_dotenv

from app_config import APP_SPEECH_GOOGLE_VOICE_CN, APP_SPEECH_GOOGLE_VOICE_ES, APP_SPEECH_GOOGLE_VOICE_FR, \
    APP_SPEECH_GOOGLE_VOICE_DE, APP_SPEECH_GOOGLE_VOICE_IN, APP_SPEECH_GOOGLE_VOICE_JP, APP_SPEECH_GOOGLE_VOICE_KR, \
    APP_SPEECH_GOOGLE_VOICE_EN

load_dotenv()

# text = "안녕하세요, 좋은 오후입니다"
# start = time.time()
# _, _, details = cld2.detect(text)
# print(details)  # 'en'
# print(details[0][1])  # 'en'
# print(f"Time: {(time.time() - start) * 1000:.3f} ms")


def detect_language_code(input: str):
    try:
        _, _, details = cld2.detect(input)
        code = details[0][1]
        if code == "un":
            return "en"
        else:
            return code
    except Exception as e:
        return "en"


def get_voice_name_by_lang_code(lang_code: str):
    if lang_code == "en":
        return APP_SPEECH_GOOGLE_VOICE_EN
    if lang_code == "zh-Hant":
        return APP_SPEECH_GOOGLE_VOICE_CN
    if lang_code == "es":
        return APP_SPEECH_GOOGLE_VOICE_ES
    if lang_code == "fr":
        return APP_SPEECH_GOOGLE_VOICE_FR
    if lang_code == "de":
        return APP_SPEECH_GOOGLE_VOICE_DE
    if lang_code == "hi":
        return APP_SPEECH_GOOGLE_VOICE_IN
    if lang_code == "ja":
        return APP_SPEECH_GOOGLE_VOICE_JP
    if lang_code == "ko":
        return APP_SPEECH_GOOGLE_VOICE_KR
    # default english
    return APP_SPEECH_GOOGLE_VOICE_EN
