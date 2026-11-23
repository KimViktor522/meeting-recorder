"""Приложение: значок в трее, главный цикл детекта и записи, диагностика, импорт, автозапуск."""
import argparse
import collections
import ctypes
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import winreg

from . import about, capture, greeting, ics, importer, process, rules, settings
from .i18n import tr

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
POLL = 3  # секунды
DEVICE_CHECK_EVERY = 5  # тиков (≈15 с): создание PortAudio дороже обычного опроса


def log(msg):
    with open(settings.data_dir() / "recorder.log", "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def launch_cmd(*args):
    """Команда запуска себя: exe (сборка PyInstaller) или pythonw run.py (из исходников)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    py = pathlib.Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    return [str(pyw if pyw.exists() else py), str(pathlib.Path(__file__).resolve().parent.parent / "run.py"), *args]


def spawn_process(folder):
    subprocess.Popen(launch_cmd("--process", str(folder)), creationflags=0x00000008 | 0x08000000, close_fds=True)


def cfg_g(cfg):
    return cfg["greeting"]


def make_icon(color):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=color)
    d.rounded_rectangle((26, 16, 38, 36), 6, fill="white")  # микрофон
    d.arc((20, 24, 44, 44), 0, 180, fill="white", width=3)
    d.line((32, 44, 32, 50), fill="white", width=3)
    return img


class App:
    def __init__(self):
        self.cfg = settings.load()
        self.session = None
        self.paused = self.stopping = self.skipped = False
        self.last_active = 0.0
        self.icon = None
        self.state = ("idle", "")
        self.ui = None
        self.log = log
        self.events = collections.deque(maxlen=60)  # (время, текст) для окна диагностики
        self.active_warns = set()
        self.dev_flag = False
        self.tick = 0

    @property
    def t(self):
        return tr(settings.ui_language(self.cfg))

    # ---- конфиг и автозапуск ----
    def apply_config(self, cfg, autostart):
        settings.save(cfg)
        self.cfg = cfg
        self.set_autostart(autostart)
        self.set_status(*self.state)

    @staticmethod
    def autostart_enabled():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, about.APP_NAME)
            return True
        except OSError:
            return False

    @staticmethod
    def set_autostart(on):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, about.APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(launch_cmd()))
            else:
                try:
                    winreg.DeleteValue(k, about.APP_NAME)
                except OSError:
                    pass

    # ---- статус, события, предупреждения ----
    def event(self, text):
        self.events.append((dt.datetime.now().strftime("%H:%M:%S"), text))
        log(text)

    def set_status(self, kind, detail=""):
        self.state = (kind, detail)
        text = {"idle": self.t("st_idle"), "paused": self.t("st_paused"), "rec": self.t("st_rec", detail),
                "listen": self.t("st_listen", detail)}[kind]
        if self.icon:
            self.icon.icon = make_icon({"idle": "#777777", "paused": "#e6a100", "rec": "#d62828", "listen": "#2a7de1"}[kind])
            self.icon.title = f"{about.APP_NAME}: {text}"
            self.icon.update_menu()

    def warn(self, code):
        text = self.t("warn_" + code)
        self.event("WARN " + code)
        try:
            if self.icon:
                self.icon.notify(text, about.APP_NAME)
        except Exception:
            pass

    def levels(self):
        s = self.session
        return s.levels() if s else None

    # ---- запись ----
    def start(self, meta, manual=False, call_key=None):
        folder = process.work_root() / f"{dt.datetime.now():%Y-%m-%d_%H-%M}_{rules.slug(meta['title'])}"
        try:
            self.session = capture.Session(folder, meta, manual=manual)
        except Exception as e:
            self.event(f"не удалось начать запись: {e!r}")
            self.session = None
            shutil.rmtree(folder, ignore_errors=True)
            self.skipped = not manual  # автозапуск не повторяем каждые 3 с: ждём конца этого звонка
            return False
        self.session.call_key = call_key
        self.last_active = time.monotonic()
        self.active_warns, self.dev_flag = set(), False
        self.set_status("rec", meta["title"])
        self.event(f"START {folder.name} rule={meta['rule']!r} action={meta['action']}")
        return True

    def finish(self, discard=False, reason=""):
        s, self.session = self.session, None
        dur = s.stop()
        minimum = 5 if s.manual else self.cfg["min_record_seconds"]
        if s.pending and not s.manual:  # приветствия так и не было — запись временная, выбрасываем
            discard, reason = True, reason or "no greeting"
        if discard or dur < minimum:
            shutil.rmtree(s.folder, ignore_errors=True)
            self.event(f"DISCARD {s.folder.name} ({reason or 'too short'}, {dur:.0f} s)")
        else:
            s.meta["end"], s.meta["seconds"] = dt.datetime.now().isoformat(timespec="seconds"), round(dur)
            (s.folder / "meta.json").write_text(json.dumps(s.meta, ensure_ascii=False, indent=2), encoding="utf-8")
            self.event(f"STOP {s.folder.name} {dur:.0f} s -> processing")
            spawn_process(s.folder)
        self.active_warns = set()
        self.set_status("paused" if self.paused else "idle")

    def start_manual(self, *_):
        if self.session:
            return
        ev = ics.event_now(self.cfg)
        title = (ev or {}).get("title") or self.t("manual_title")
        meta = {"title": title, "attendees": (ev or {}).get("attendees", []), "titles": [], "rule": "manual", "action": "manual",
                "project_rule": None, "start": dt.datetime.now().isoformat(timespec="seconds")}
        self.start(meta, manual=True)

    def stop_manual(self, *_):
        if self.session:
            self.finish(reason="manual stop")

    def decide_and_start(self, call_key, titles):
        ev = ics.event_now(self.cfg)
        title = (ev or {}).get("title") or (titles[0] if titles else "Meeting")
        text = " ".join([title] + (ev or {}).get("attendees", []) + titles)
        action, rule, project = rules.decide(text, self.cfg)
        self.event(f"CALL app={call_key} titles={titles} event={title!r} -> {action} {rule!r}")
        if action == "skip":
            self.skipped = True
            return
        meta = {"title": title, "attendees": (ev or {}).get("attendees", []), "titles": titles, "rule": rule, "action": action,
                "project_rule": project, "start": dt.datetime.now().isoformat(timespec="seconds")}
        if self.start(meta, call_key=call_key):
            if cfg_g(self.cfg)["enabled"]:
                self.arm_greeting(self.session)
            if action == "ask":
                session = self.session
                self.ui.ask(title, lambda choice: self.answer(session, choice))

    def arm_greeting(self, session):
        """Запись временная, пока не прозвучит приветствие (или не истечёт запасное время)."""
        g = cfg_g(self.cfg)
        session.pending = True
        session.deadline = time.monotonic() + g["fallback_min"] * 60 if g["fallback_min"] > 0 else None
        self.set_status("listen", session.meta["title"])
        self.event("LISTEN for greeting")
        threading.Thread(target=greeting.listen, daemon=True, args=(
            session, self.cfg, lambda why: self.activate(session, why), log, lambda: self.session is session)).start()

    def activate(self, session, why):
        """Приветствие услышано (или сработал запас): запись становится постоянной."""
        if self.session is session and session.pending:
            session.pending = False
            session.meta["trigger"] = why
            self.set_status("rec", session.meta["title"])
            self.event(f"GREETING -> recording ({why})")

    def answer(self, session, choice):
        self.event(f"ANSWER {choice}")
        if choice == "skip" and self.session is session and session is not None:
            self.skipped = True
            self.finish(discard=True, reason="user choice")

    def check_health(self):
        """Предупреждения во время записи: тишина, остановка потока, смена устройства. Каждое срабатывает один раз за эпизод."""
        if self.tick % DEVICE_CHECK_EVERY == 0:
            self.dev_flag = "device_changed" in self.session.check(devices=True)
        codes = set(self.session.check()) | ({"device_changed"} if self.dev_flag else set())
        for code in codes - self.active_warns:
            self.warn(code)
        self.active_warns = codes

    def loop(self):
        streak = 0
        while not self.stopping:
            self.tick += 1
            try:
                cfg, s = self.cfg, self.session
                snap = capture.snapshot(cfg)
                now = time.monotonic()
                if s:
                    if s.pending and s.deadline and now > s.deadline:
                        self.activate(s, "timeout")
                    if not s.pending:
                        self.check_health()
                    if not s.manual:  # ручная запись останавливается только кнопкой
                        if any(k == s.call_key for k, _ in snap["users"]):
                            self.last_active = now
                        elif now - self.last_active > cfg["mic_release_grace_seconds"]:
                            self.finish()
                elif self.skipped:
                    self.skipped = bool(snap["users"])  # приложение отпустило микрофон — готовы к следующей встрече
                elif snap["call"] and not self.paused:
                    streak += 1
                    if streak >= 2:  # звонок держится >= 2 опросов подряд
                        streak = 0
                        self.decide_and_start(*snap["call"])
                else:
                    streak = 0
            except Exception as e:
                log(f"loop error: {e!r}")
            time.sleep(POLL)
        if self.session:
            self.finish()

    # ---- импорт ----
    def import_files(self, media, transcript, title, date, project, keep):
        """Ставит импорт в очередь обработки. Бросает исключение с понятным текстом при ошибке в файлах."""
        work = importer.import_meeting(self.cfg, media or None, transcript or None, title or None, date or None, project or None, keep)
        self.event(f"IMPORT {work.name}")
        spawn_process(work)
        return work

    # ---- меню трея ----
    def toggle_pause(self, *_):
        self.paused = not self.paused
        if not self.session:
            self.set_status("paused" if self.paused else "idle")

    def skip_current(self, *_):
        if self.session:
            self.skipped = True
            self.finish(discard=True, reason="tray skip")

    def quit(self, *_):
        self.stopping = True
        self.icon.stop()

    def run(self):
        import pystray
        from .ui import UI
        P = pystray
        menu = P.Menu(
            P.MenuItem(lambda _: self.t("tray_settings"), lambda *_: self.ui.open_settings(), default=True),
            P.MenuItem(lambda _: self.t("tray_diag"), lambda *_: self.ui.open_diag()),
            P.MenuItem(lambda _: self.t("tray_import"), lambda *_: self.ui.open_import()),
            P.Menu.SEPARATOR,
            P.MenuItem(lambda _: self.t("tray_start"), self.start_manual, visible=lambda _: self.session is None),
            P.MenuItem(lambda _: self.t("tray_stop"), self.stop_manual, visible=lambda _: self.session is not None),
            P.MenuItem(lambda _: self.t("tray_resume") if self.paused else self.t("tray_pause"), self.toggle_pause),
            P.MenuItem(lambda _: self.t("tray_skip"), self.skip_current, visible=lambda _: self.session is not None),
            P.Menu.SEPARATOR,
            P.MenuItem(lambda _: self.t("tray_folder"), lambda *_: (settings.output_dir(self.cfg).mkdir(parents=True, exist_ok=True), os.startfile(settings.output_dir(self.cfg)))),
            P.MenuItem(lambda _: self.t("tray_logs"), lambda *_: os.startfile(settings.data_dir())),
            P.Menu.SEPARATOR,
            P.MenuItem(lambda _: self.t("tray_quit"), self.quit))
        self.ui = UI(self)
        self.ui.start()
        self.ui.ready.wait()
        self.icon = P.Icon(about.APP_NAME, make_icon("#777777"), about.APP_NAME, menu)
        process.work_root()
        resume_unprocessed()
        threading.Thread(target=self.loop, daemon=True).start()
        if not self.cfg["first_run_done"]:
            self.ui.welcome()
        self.icon.run()


def resume_unprocessed():
    """Записи и импорты, обработка которых прервалась (сбой, перезагрузка), запускаются заново."""
    for d in sorted(process.work_root().glob("*")):
        if (d / "meta.json").exists():
            log(f"RESUME {d.name}")
            spawn_process(d)


def single_instance():
    """Один экземпляр на пользователя (именованный mutex). Возвращает False, если уже запущено."""
    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = ctypes.c_void_p
    handle = k.CreateMutexW(None, False, "Local\\MeetingRecorderSingleInstance")
    single_instance.handle = handle  # держим до конца процесса
    return k.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def cli_import(argv):
    ap = argparse.ArgumentParser(prog="MeetingRecorder --import", description="Import a ready recording and/or transcript into the pipeline")
    ap.add_argument("media", nargs="?", help="video/audio file (optional if --transcript is given)")
    ap.add_argument("--transcript"), ap.add_argument("--title"), ap.add_argument("--project"), ap.add_argument("--date", help="YYYY-MM-DD[THH:MM]")
    ap.add_argument("--keep-original", action="store_true")
    a = ap.parse_args(argv)
    cfg = settings.load()
    work = importer.import_meeting(cfg, a.media, a.transcript, a.title, a.date, a.project, a.keep_original)
    final = process.run_locked(process.process, work, cfg)
    print(final)


def cli_diagnose():
    """Во время звонка: что видит детектор (10 замеров по 3 с). Результат — в консоль и в diagnose.log."""
    cfg = settings.load()
    path = settings.data_dir() / "diagnose.log"
    for _ in range(10):
        text = f"{dt.datetime.now():%H:%M:%S}\n{capture.describe(cfg, settings.ui_language(cfg))}\n"
        print(text, flush=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        time.sleep(POLL)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--process"]:
        cfg = settings.load()
        process.run_locked(process.process, pathlib.Path(argv[1]), cfg)
    elif argv[:1] == ["--retry"]:
        process.run_locked(process.retry, settings.load())
    elif argv[:1] == ["--import"]:
        cli_import(argv[1:])
    elif argv[:1] == ["--diagnose"]:
        cli_diagnose()
    else:
        if not single_instance():
            return
        App().run()
