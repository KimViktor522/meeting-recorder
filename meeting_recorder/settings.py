r"""Конфиг пользователя (%APPDATA%\MeetingRecorder\config.json), значения по умолчанию, шифрование ключей (DPAPI)."""
import base64
import copy
import ctypes
import json
import os
import pathlib
from ctypes import wintypes

APP = "MeetingRecorder"

PRESETS = {  # провайдер -> (base_url, модель по умолчанию)
    "zai": ("https://api.z.ai/api/paas/v4", "glm-4.6"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    "ollama": ("http://localhost:11434/v1", "llama3.1"),
    "custom": ("", ""),
    "anthropic": ("https://api.anthropic.com", "claude-sonnet-5-5"),
    "claude_cli": ("", ""),
    "none": ("", ""),
}

DEFAULTS = {
    "version": 1,
    "language": "auto",  # auto | ru | en
    "first_run_done": False,
    "output_dir": "",  # пусто = Документы\MeetingRecorder
    "services": ["Telemost", "Телемост", "Zoom", "Google Meet", "Microsoft Teams", "Bitrix24", "Битрикс", "Skype", "Discord"],
    "ignore_mic_apps": ["python", "vosk", "voice_typing"],
    "rules": [],  # {"name","match":[..],"action":"record|skip","project":""}
    "projects": [],  # {"name","path":"","keywords":[..]}
    "default_action": "ask",  # ask | record | skip — для встреч без правила
    "ask_timeout_sec": 30,
    "default_on_timeout": "record",
    "min_record_seconds": 60,
    "mic_release_grace_seconds": 25,
    "calendar": {"enabled": False, "ics_url_enc": ""},
    "whisper": {"model": "small", "language": "auto"},
    "greeting": {"enabled": False, "fallback_min": 3, "model": "base",
                 "phrases": ["добрый день", "добрый вечер", "доброе утро", "здравствуйте", "здравствуй", "привет", "всем привет",
                             "приветствую", "good morning", "good afternoon", "good evening", "hello everyone", "hi everyone"]},
    "protocol": {"provider": "none", "base_url": "", "model": "", "api_key_enc": "", "prompt": ""},
}


def data_dir():
    p = pathlib.Path(os.environ.get("MR_HOME") or os.path.join(os.environ.get("APPDATA") or str(pathlib.Path.home()), APP))
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path():
    return data_dir() / "config.json"


def load():
    cfg = copy.deepcopy(DEFAULTS)
    try:
        user = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def save(cfg):
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # атомарно: сбой посреди записи не портит конфиг


def output_dir(cfg):
    return pathlib.Path(cfg["output_dir"]) if cfg.get("output_dir") else pathlib.Path.home() / "Documents" / APP


def ui_language(cfg):
    lang = cfg.get("language", "auto")
    if lang in ("ru", "en"):
        return lang
    try:
        return "ru" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x19 else "en"
    except Exception:
        return "en"


# ---- DPAPI: ключ API шифруется ключом пользователя Windows ----
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data, protect):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR if protect else ctypes.c_void_p, ctypes.POINTER(_Blob),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI error")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(text):
    return "dpapi:" + base64.b64encode(_crypt(text.encode("utf-8"), True)).decode() if text else ""


def unprotect(blob):
    if not blob:
        return ""
    try:
        return _crypt(base64.b64decode(blob.removeprefix("dpapi:")), False).decode("utf-8")
    except Exception:
        return ""  # чужой профиль/повреждено — ключ придётся ввести заново


def api_key(cfg):
    return unprotect(cfg["protocol"].get("api_key_enc", "")) or os.environ.get("MR_API_KEY", "")
