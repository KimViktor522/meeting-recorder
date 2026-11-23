"""Ожидание приветствия: пока запись «временная», каждые несколько секунд распознаём последние секунды звука (лёгкой моделью)
и ищем фразы-приветствия. Нашли — запись становится постоянной."""
import time

from . import models, rules

WINDOW = 10   # секунд звука на одно распознавание
EVERY = 7     # секунд между распознаваниями
MIN_SOUND = 0.004


def listen(session, cfg, on_hit, log, is_current):
    """Работает в потоке до приветствия, до конца ожидания (session.pending == False) или до смены сессии."""
    g = cfg["greeting"]
    try:
        model = models.load_model(g["model"])
    except Exception as e:  # нет модели/сети: лучше писать сразу, чем потерять встречу
        log(f"greeting model unavailable: {e!r}")
        on_hit("model-unavailable")
        return
    wl = cfg["whisper"].get("language", "auto")
    while session.pending and is_current():
        time.sleep(EVERY)
        if not (session.pending and is_current()):
            break
        for track in session.tracks:
            audio = track.tail(WINDOW)
            if len(audio) < 16000 or abs(audio).max() < MIN_SOUND:
                continue
            try:
                it, _ = model.transcribe(audio, language=None if wl in ("auto", "") else wl, vad_filter=True, beam_size=1,
                                         condition_on_previous_text=False)
                text = " ".join(s.text for s in it)
            except Exception as e:
                log(f"greeting transcribe error: {e!r}")
                continue
            hit = rules.greeting_hit(text, g["phrases"])
            if hit:
                log(f"greeting heard: {text.strip()[:80]!r}")
                on_hit(hit)
                return
