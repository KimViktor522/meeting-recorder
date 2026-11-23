"""Тесты этапа 3: привязка окна к процессу звонка, предупреждения о тишине, проверяемый протокол, импорт. python tests/test_stage3.py"""
import json
import os
import pathlib
import sys
import tempfile
import types
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MR_HOME"] = tempfile.mkdtemp(prefix="mr_s3_")

from meeting_recorder import capture, importer, llm, models, process, settings  # noqa: E402

# ---------- C1: окно должно принадлежать приложению, которое держит микрофон ----------
CODE = ("C:#Users#u#AppData#Local#Programs#Microsoft VS Code#Code.exe", r"C:\Users\u\AppData\Local\Programs\Microsoft VS Code\Code.exe")
CHROME = ("C:#Program Files#Google#Chrome#Application#chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
SVC = ["Телемост", "Zoom"]
# инцидент: микрофон держит VS Code, «Телемост» — в заголовке окна Проводника
assert capture.match_call([CODE], [("Телемост - Проводник", r"C:\Windows\explorer.exe"), ("main.py - VS Code", CODE[1])], SVC) is None
# окно сервиса есть, но у процесса без доступа к микрофону — не звонок
assert capture.match_call([CODE], [("Телемост", CHROME[1])], SVC) is None
# браузер держит микрофон и показывает вкладку сервиса -> звонок
assert capture.match_call([CODE, CHROME], [("Телемост — встреча — Google Chrome", CHROME[1])], SVC) == (CHROME[0], ["Телемост — встреча — Google Chrome"])
assert capture.match_call([CHROME], [("TELEMOST", CHROME[1].upper())], ["telemost"]) is not None  # регистр не важен
# упакованное (Store) приложение: ключ — имя пакета, путь exe содержит его
assert capture.match_call([("MSTeams_8wekyb3d8bbwe", None)], [("Standup | Zoom-like", r"C:\Program Files\WindowsApps\MSTeams_25.1_x64__8wekyb3d8bbwe\ms-teams.exe")], ["Standup"])
assert capture.match_call([("MSTeams_8wekyb3d8bbwe", None)], [("Standup", r"C:\Windows\explorer.exe")], ["Standup"]) is None
assert capture.match_call([], [("Телемост", CHROME[1])], SVC) is None
assert capture.match_call([CHROME], [("Телемост", None)], SVC) is None  # процесс окна неизвестен — не гадаем

# ---------- C3: предупреждения о тишине ----------
assert capture.silence_warnings(100, 0, 90, 90, 60, 120) == []
assert capture.silence_warnings(100, 0, 10, 90, 60, 120) == ["mic_silent"]
assert capture.silence_warnings(200, 0, 190, 10, 60, 120) == ["system_silent"]
assert capture.silence_warnings(30, 0, 0, 0, 60, 120) == []  # рано: запись только началась

# ---------- C5: разбор готовых транскриптов ----------
tmp = pathlib.Path(tempfile.mkdtemp(prefix="mr_files_"))
vtt = tmp / "zoom.vtt"
vtt.write_text("WEBVTT\n\n1\n00:00:05.000 --> 00:00:08.000\n<v Анна>Начинаем встречу</v>\n\n2\n00:01:10.500 --> 00:01:12.000\nИван: Решили перенести срок\n", encoding="utf-8")
s = importer.parse_transcript(vtt)
assert [(x["start"], x["who"], x["text"]) for x in s] == [(5.0, "Анна", "Начинаем встречу"), (70.0, "Иван", "Решили перенести срок")], s
srt = tmp / "a.srt"
srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nПривет\n\n2\n01:02:03,000 --> 01:02:04,000\nПока\n", encoding="utf-8")
assert [x["start"] for x in importer.parse_transcript(srt)] == [1.0, 3723.0]
txt = tmp / "t.txt"
txt.write_text("[00:05] Анна: Первый пункт\n[01:30] Второй пункт\n\nПётр Сидоров 02:00\nМы согласны\nс условиями\n", encoding="utf-8")
s = importer.parse_transcript(txt)
assert (s[0]["start"], s[0]["who"]) == (5.0, "Анна") and s[1]["start"] == 90.0 and s[1]["who"] is None
assert (s[2]["start"], s[2]["who"], s[2]["text"]) == (120.0, "Пётр Сидоров", "Мы согласны с условиями"), s
plain = tmp / "p.txt"
plain.write_text("Просто текст без времени\nЕщё строка\n", encoding="utf-8")
assert all(x["start"] is None for x in importer.parse_transcript(plain))
empty = tmp / "e.txt"
empty.write_text("\n\n", encoding="utf-8")
try:
    importer.parse_transcript(empty)
    raise AssertionError("пустой транскрипт должен отклоняться")
except ValueError:
    pass

# ---------- C4: проверка ссылок в протоколе ----------
times = [5, 70, 130]
body = """## Итоги
- Обсудили план [00:05]

## Решения
- Перенести срок [01:10]
- Взять подрядчика без ссылки
- Выдуманное решение [09:59]

## Задачи
| Задача | Ответственный | Срок | Источник |
|---|---|---|---|
| Подготовить КП | Анна | пятница | [02:10] |
| Нет источника | Иван | - | |

## Открытые вопросы
- Нет
"""
out, ok, bad, unsrc = process.verify_refs(body, times, "ru")
assert (ok, bad, unsrc) == (3, 1, 3), (ok, bad, unsrc, out)  # 00:05, 01:10, 02:10 верны; 09:59 снята; 3 пункта без источника
assert "[01:10](audio.m4a#t=70)" in out and "[00:05](audio.m4a#t=5)" in out and "09:59" not in out
assert out.count("⚠ нет источника") == 3 and "| Нет источника | Иван | - | ⚠" in out.replace("  ", " ") or "⚠ нет источника |" in out
assert "- Нет" in out and "Проверка источников: подтверждено ссылок — 3, снято несуществующих — 1, пунктов без источника — 3." in out
assert process.verify_refs("## Decisions\n- x [01:12]\n", [70], "en")[1] == 1  # 2 с от реплики — «прилипает» к 01:10
assert "[01:10]" in process.verify_refs("## Decisions\n- x [01:12]\n", [70], "en", link_audio=False)[0]
assert process.transcript_times("[00:05] a\n[01:10] **Я:** b\nno time\n") == [5, 70]

# ---------- C5: сквозной импорт ----------
cfg = settings.load()
cfg["output_dir"] = str(tmp / "out")
cfg["projects"] = [{"name": "Alpha", "path": "", "keywords": ["alpha"]}]
cfg["protocol"].update(provider="custom", base_url="http://unused", model="m")
MD_SEEN = []
llm.complete = lambda c, instruction, text: (MD_SEEN.append((instruction, text)) or "## Решения\n- Взять срок [01:10]\n- Левое [50:00]\n")


class FakeModel:
    def transcribe(self, a, **kw):
        return iter([types.SimpleNamespace(start=2.0, text=" alpha первый"), types.SimpleNamespace(start=71.0, text=" alpha второй alpha")]), None


models.load_model = lambda name: FakeModel()
wav = tmp / "call.wav"
with wave.open(str(wav), "wb") as w:
    w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b"\x00\x00" * 16000 * 3)

# а) медиа + готовый транскрипт: распознавание не запускается, копия исходника по желанию
models.load_model = lambda name: (_ for _ in ()).throw(AssertionError("whisper не должен запускаться при готовом транскрипте"))
work = importer.import_meeting(cfg, media=wav, transcript=vtt, title="Alpha sync", date="2026-05-04T10:00", keep_original=True)
final = process.process(work, cfg)
assert final.parent.parent.name == "Alpha" and final.name.startswith("2026-05-04_Alpha_sync"), final  # проект — по названию встречи
for name in ("audio.m4a", "transcript.md", "protocol.md", "source.wav", "meta.json"):
    assert (final / name).exists(), name
assert not (final / "mono16.wav").exists() and wav.exists()  # исходник не тронут
t = (final / "transcript.md").read_text(encoding="utf-8")
assert "[00:05] **Анна:** Начинаем встречу" in t
prot = (final / "protocol.md").read_text(encoding="utf-8")
assert "[01:10](audio.m4a#t=70)" in prot and "50:00" not in prot and "Проверка источников: подтверждено ссылок — 1, снято несуществующих — 1" in prot
assert "[мм:сс]" in MD_SEEN[-1][0]  # промт требует источники

# б) только медиа: whisper по моно, без имён говорящих
models.load_model = lambda name: FakeModel()
work = importer.import_meeting(cfg, media=wav, title="Только звук", date="2026-05-05")
final2 = process.process(work, cfg)
assert final2.parent.parent.name == "Alpha"  # по словам в разговоре (3 совпадения)
assert (final2 / "transcript.md").read_text(encoding="utf-8").startswith("[00:02] alpha первый")
assert not (final2 / "source.wav").exists()  # без keep_original копии нет

# в) только транскрипт без времени: протокол без проверки ссылок, аудио нет
work = importer.import_meeting(cfg, transcript=plain, title="Без времени", date="2026-05-06", project="Alpha")
final3 = process.process(work, cfg)
assert not (final3 / "audio.m4a").exists() and "Проверка источников" not in (final3 / "protocol.md").read_text(encoding="utf-8")
assert "НЕТ таймкодов" in MD_SEEN[-1][0]

# г) ошибки ввода
for bad_args in ({}, {"media": tmp / "nope.mp4"}, {"media": txt}):
    try:
        importer.import_meeting(cfg, **bad_args)
        raise AssertionError(bad_args)
    except (ValueError, FileNotFoundError):
        pass
# ---------- приветствия как триггер записи ----------
from meeting_recorder import rules  # noqa: E402
ph = ["добрый день", "здравствуйте", "привет", "good morning"]
assert rules.greeting_hit("Ну что, Добрый день, коллеги!", ph) == "добрый день"
assert rules.greeting_hit("ЗДРАВСТВУЙТЕ.", ph) == "здравствуйте"
assert rules.greeting_hit("Всем привет", ph) == "привет"
assert rules.greeting_hit("Приветствую", ph) is None  # только целыми словами
assert rules.greeting_hit("Good morning, everyone", ph) == "good morning"
assert rules.greeting_hit("обсуждаем договор", ph) is None and rules.greeting_hit("текст", [" ", ""]) is None
# хвост звука: ресемплинг 48 кГц стерео -> 16 кГц моно
import numpy as np, time, collections  # noqa: E402
tr = capture.Track.__new__(capture.Track)
tr.ring, tr.rate, tr.ch = collections.deque(), 48000, 2
now = time.monotonic()
tr.ring.append((now - 1, (np.ones(48000 * 2) * 3277).astype(np.int16)))  # 1 с «звука» (0.1 амплитуды)
tail = tr.tail(5)
assert abs(len(tail) - 16000) < 5 and abs(tail.mean() - 0.1) < 0.01 and tail.dtype == np.float32
assert len(capture.Track.tail(types.SimpleNamespace(ring=collections.deque(), rate=48000, ch=2), 5)) == 0
print("test_stage3 OK")
