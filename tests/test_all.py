"""Тесты без фреймворков: python tests/test_all.py (нужны установленные зависимости из requirements.txt)."""
import datetime as dt
import http.server
import json
import os
import pathlib
import sys
import tempfile
import threading
import types
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MR_HOME"] = tempfile.mkdtemp(prefix="mr_test_")  # свой каталог данных, чужой конфиг не трогаем

from meeting_recorder import ics, llm, models, process, rules, settings  # noqa: E402

# ---------- настройки и шифрование ----------
cfg = settings.load()
assert cfg["protocol"]["provider"] == "none" and cfg["whisper"]["model"] == "small"
secret = "sk-тест-123"
enc = settings.protect(secret)
assert enc.startswith("dpapi:") and secret not in enc and settings.unprotect(enc) == secret
assert settings.unprotect("dpapi:AAAA") == ""  # мусор не роняет приложение
cfg["language"] = "en"
settings.save(cfg)
assert settings.load()["language"] == "en" and settings.load()["whisper"]["model"] == "small"  # слияние с дефолтами

# ---------- правила ----------
cfg["rules"] = [
    {"name": "uni", "match": ["занятие", "lecture"], "action": "skip", "project": ""},
    {"name": "standup", "match": ["стендап"], "action": "record", "project": "Alpha"},
]
cfg["projects"] = [{"name": "Alpha", "path": "", "keywords": ["alpha", "erp"]}, {"name": "Beta", "path": "", "keywords": ["beta"]}]
assert rules.decide("Занятие по менеджменту", cfg)[0] == "skip"
assert rules.decide("Утренний СТЕНДАП", cfg) == ("record", "standup", "Alpha")
assert rules.decide("lunch", cfg)[0] == "ask"
assert rules.decide("", {**cfg, "rules": [{"name": "x", "match": [" "], "action": "skip"}]})[0] == "ask"  # пустое слово не совпадает
assert rules.pick_project("alpha erp alpha", cfg)[0] == "Alpha"
assert rules.pick_project("nothing", cfg) == (None, None)
p, hint = rules.pick_project("alpha beta", cfg)
assert p is None and hint in ("Alpha", "Beta")
assert str(rules.meetings_root(None, cfg)).endswith(os.path.join("_Inbox", "Meetings"))
assert rules.slug('A/B: "C"?') == "A_B_C"

# ---------- календарь (ICS с повторением) ----------
now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
start = (now - dt.timedelta(minutes=5)).strftime("%Y%m%dT%H%M%SZ")
end = (now + dt.timedelta(minutes=25)).strftime("%Y%m%dT%H%M%SZ")
first = (now - dt.timedelta(days=3, minutes=5)).strftime("%Y%m%dT%H%M%SZ")  # серия началась 3 дня назад
ICS = f"""BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTAMP:{start}\r\nDTSTART:{first}\r\nDTEND:{(now - dt.timedelta(days=3) + dt.timedelta(minutes=25)).strftime("%Y%m%dT%H%M%SZ")}\r\nRRULE:FREQ=DAILY\r\nSUMMARY:Daily standup\r\nATTENDEE;CN=Ann:mailto:ann@example.com\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:2\r\nDTSTAMP:{start}\r\nDTSTART;VALUE=DATE:{now:%Y%m%d}\r\nSUMMARY:All day (ignored)\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""
ev = ics.current_event(ICS, now)
assert ev and ev["title"] == "Daily standup" and "ann@example.com" in ev["attendees"][0], ev
assert ics.current_event(ICS, now + dt.timedelta(hours=5)) is None

# ---------- провайдеры протокола на локальном mock-сервере ----------
seen = []


class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append((self.path, self.headers.get("Authorization"), self.headers.get("x-api-key"), body))
        if self.path.endswith("/chat/completions"):
            out = {"choices": [{"message": {"content": "## OK openai"}}]}
        else:
            out = {"content": [{"type": "text", "text": "## OK anthropic"}]}
        data = json.dumps(out).encode()
        self.send_response(200), self.send_header("Content-Length", str(len(data))), self.end_headers(), self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}"
c = {"protocol": {"provider": "custom", "base_url": url + "/v1", "model": "m", "api_key_enc": settings.protect("K1"), "prompt": ""}}
assert llm.complete(c, "inst", "text") == "## OK openai"
assert seen[-1][0] == "/v1/chat/completions" and seen[-1][1] == "Bearer K1" and "text" in seen[-1][3]["messages"][0]["content"]
c["protocol"].update(provider="anthropic", base_url=url, api_key_enc=settings.protect("K2"))
assert llm.complete(c, "inst", "text") == "## OK anthropic" and seen[-1][2] == "K2" and seen[-1][0] == "/v1/messages"
c["protocol"].update(provider="openai", base_url=url + "/v1", api_key_enc="")
ok, msg = llm.test(c)
assert not ok and "key" in msg.lower()  # облачному провайдеру без ключа — понятная ошибка, запрос не уходит
c["protocol"].update(provider="ollama", api_key_enc="")
assert llm.test(c) == (True, "## OK openai")  # локальному ключ не нужен
srv.shutdown()

# ---------- конвейер обработки (распознавание подменено, ffmpeg настоящий) ----------
out_dir = pathlib.Path(tempfile.mkdtemp(prefix="mr_out_"))
cfg.update(output_dir=str(out_dir), rules=cfg["rules"])
cfg["protocol"] = {"provider": "none", "base_url": "", "model": "", "api_key_enc": "", "prompt": ""}


class FakeModel:
    def transcribe(self, audio, **kw):
        seg = types.SimpleNamespace(start=1.0, text=" Обсуждаем alpha и ERP, потом alpha снова ")
        return iter([seg]), None


models.load_model = lambda name: FakeModel()
work = pathlib.Path(process.work_root()) / "2026-01-01_10-00_t"
work.mkdir()
for n in ("mic", "system"):
    with wave.open(str(work / f"{n}.wav"), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(44100), w.writeframes(b"\x00\x00" * 2 * 44100)
(work / "meta.json").write_text(json.dumps({"title": "Sync", "attendees": [], "titles": [], "project_rule": None, "start": "2026-01-01T10:00:00", "seconds": 90}), encoding="utf-8")
final = process.process(work, cfg)
assert (final / "audio.m4a").exists() and (final / "transcript.md").exists() and not (final / "mic.wav").exists() and not work.exists()
assert final.parent == out_dir / "Alpha" / "Meetings", final  # проект выбран по словам разговора (3 совпадения)
assert not (final / "protocol.md").exists()
print("test_all OK")
