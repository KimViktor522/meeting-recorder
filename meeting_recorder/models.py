"""Каталог моделей распознавания (faster-whisper) и их загрузка."""
CATALOG = [  # (id, размер, скорость на CPU, качество)
    ("tiny", "75 MB", 5, 1),
    ("base", "145 MB", 4, 2),
    ("small", "485 MB", 3, 3),
    ("medium", "1.5 GB", 2, 4),
    ("large-v3-turbo", "1.6 GB", 2, 5),
    ("large-v3", "3 GB", 1, 5),
]


def is_downloaded(name):
    try:
        from faster_whisper.utils import download_model
        download_model(name, local_files_only=True)
        return True
    except Exception:
        return False


def download(name):
    """Скачивает модель с Hugging Face (блокирующий вызов — запускать в потоке). Бросает исключение при ошибке."""
    from faster_whisper.utils import download_model
    return download_model(name)


def load_model(name):
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(name, device="cpu", compute_type="int8", local_files_only=True)
    except Exception:
        return WhisperModel(name, device="cpu", compute_type="int8")  # нет в кэше — докачаем
