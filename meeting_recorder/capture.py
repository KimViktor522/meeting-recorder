"""Захват звука и детект звонка (Windows): микрофон + системный звук (WASAPI loopback) в два WAV.

Звонок = окно с названием сервиса, которое принадлежит ТОМУ ЖЕ приложению, что держит микрофон (сравнение по exe)."""
import collections
import ctypes
import os
import pathlib
import sys
import threading
import time
import wave
import winreg
from ctypes import wintypes

import numpy as np

CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
RING_SECONDS = 20
SOUND_LEVEL = 0.004  # пик выше этого считаем звуком (≈ -48 dBFS)


# ---------- детект ----------
def mic_users(ignore):
    """Приложения, которые сейчас держат микрофон (LastUsedTimeStop == 0): [(ключ, путь к exe | None)].
    Для упакованных (Store) приложений путь неизвестен, ключ — имя пакета."""
    ignore = [x.strip().lower() for x in ignore if x.strip()] + [pathlib.Path(sys.executable).name.lower()]  # себя не считаем
    found = []
    for sub in (CONSENT, CONSENT + r"\NonPackaged"):
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(root)[0]):
            name = winreg.EnumKey(root, i)
            if name == "NonPackaged" or any(x in name.lower() for x in ignore):
                continue
            try:
                key = winreg.OpenKey(root, name)
                if winreg.QueryValueEx(key, "LastUsedTimeStop")[0] == 0 and winreg.QueryValueEx(key, "LastUsedTimeStart")[0] != 0:
                    found.append((name, name.replace("#", "\\") if "#" in name else None))
            except OSError:
                continue
    return found


def _exe_of_hwnd(hwnd):
    k, u = ctypes.windll.kernel32, ctypes.windll.user32
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k.OpenProcess.restype = wintypes.HANDLE
    h = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        return buf.value if k.QueryFullProcessImageNameW(wintypes.HANDLE(h), 0, buf, ctypes.byref(size)) else None
    finally:
        k.CloseHandle(wintypes.HANDLE(h))


def windows_with_exe():
    """Видимые окна с заголовком: [(заголовок, путь к exe процесса окна)]."""
    user32, out = ctypes.windll.user32, []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append((buf.value, _exe_of_hwnd(hwnd)))
        return True

    user32.EnumWindows(cb, 0)
    return out


def same_app(key, key_path, exe):
    """Окно принадлежит приложению, которое держит микрофон? exe — путь процесса окна."""
    if not exe:
        return False
    exe_l = exe.lower()
    if key_path:
        return exe_l == key_path.lower()
    family = key.split("_")[0].lower()  # упакованное приложение: путь содержит имя пакета
    return len(family) >= 3 and ("\\" + family + "_") in exe_l


def match_call(users, windows, services):
    """Чистая логика: (ключ приложения, [заголовки]) для звонка либо None.
    Нужны одновременно: приложение держит микрофон И его же окно содержит название сервиса."""
    svc = [s.strip().lower() for s in services if s.strip()]
    for key, key_path in users:
        titles = [t for t, exe in windows if same_app(key, key_path, exe) and any(s in t.lower() for s in svc)]
        if titles:
            return key, titles
    return None


def snapshot(cfg):
    """Всё, что видит детектор: {'users','windows','call'}. Тест-хук MR_TEST_FLAG: файл, 1-я строка — «приложение»,
    остальные — заголовки (считаются окнами этого приложения)."""
    flag = os.environ.get("MR_TEST_FLAG")
    if flag and pathlib.Path(flag).exists():
        lines = pathlib.Path(flag).read_text(encoding="utf-8").splitlines()
        app = (lines[0] if lines else "") or None
        titles = lines[1:]
        return {"users": [(app, None)] if app else [], "windows": [(t, None) for t in titles],
                "call": (app, titles) if app and titles else None}
    users = mic_users(cfg["ignore_mic_apps"])
    windows = windows_with_exe() if users else []
    return {"users": users, "windows": windows, "call": match_call(users, windows, cfg["services"])}


def describe(cfg, lang="ru"):
    """Текст для диагностики: что видит детектор прямо сейчас."""
    ru = lang == "ru"
    s = snapshot(cfg)
    svc = [x.strip().lower() for x in cfg["services"] if x.strip()]
    none = "нет" if ru else "none"
    windows = s["windows"] if s["users"] else windows_with_exe()  # без приложений с микрофоном окна не собирались
    cand = [(t, e) for t, e in windows if any(x in t.lower() for x in svc)]
    lines = [("Приложения с микрофоном: " if ru else "Apps using the microphone: ") + (", ".join(k.split("#")[-1] for k, _ in s["users"]) or none),
             ("Окна с названием сервиса: " if ru else "Windows with a call-app name: ")
             + ("; ".join(f"«{t}» <- {pathlib.Path(e).name if e else '?'}" for t, e in cand) or none)]
    if s["call"]:
        lines.append(("Решение: ЗВОНОК (" if ru else "Decision: CALL (") + s["call"][0].split("#")[-1] + ")")
    else:
        lines.append("Решение: не звонок (нужно, чтобы приложение с микрофоном и окно сервиса совпали)" if ru else
                     "Decision: not a call (the app using the microphone must own the call-app window)")
    return chr(10).join(lines)


def detect(cfg):
    """(приложение, заголовки окон сервиса) или (None, [])."""
    call = snapshot(cfg)["call"]
    return call if call else (None, [])


def default_device_names():
    """Имена устройств по умолчанию (микрофон, loopback) — по свежему экземпляру PortAudio (старый кэширует список)."""
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        return (pa.get_device_info_by_index(wasapi["defaultInputDevice"])["name"], pa.get_default_wasapi_loopback()["name"])
    finally:
        pa.terminate()


# ---------- запись ----------
class Track:
    """Один поток PortAudio -> WAV. Loopback не шлёт данные в тишине, поэтому недостающее добиваем нулями по часам.
    Следит за уровнем звука для диагностики."""

    def __init__(self, pa, dev, path):
        import pyaudiowpatch as pyaudio
        self.name = dev["name"]
        self.rate, self.ch = int(dev["defaultSampleRate"]), dev["maxInputChannels"]
        self.wav = wave.open(str(path), "wb")
        self.wav.setnchannels(self.ch), self.wav.setsampwidth(2), self.wav.setframerate(self.rate)
        self.written, self.t0, self.lock = 0, time.monotonic(), threading.Lock()
        self.level, self.last_cb, self.last_sound = 0.0, 0.0, self.t0
        self.ring = collections.deque()  # (время, int16-массив): последние ~20 с для распознавания приветствия
        self.stream = pa.open(format=pyaudio.paInt16, channels=self.ch, rate=self.rate, input=True,
                              input_device_index=dev["index"], frames_per_buffer=1024, stream_callback=self._cb)

    def _pad(self, upto):
        gap = upto - self.written
        if gap > 0:
            self.wav.writeframes(b"\x00" * gap * self.ch * 2)
            self.written += gap

    def _cb(self, data, frames, t, status):
        import pyaudiowpatch as pyaudio
        now = time.monotonic()
        peak = float(np.abs(np.frombuffer(data, dtype=np.int16)).max()) / 32768.0 if data else 0.0
        self.level, self.last_cb = max(peak, self.level * 0.85), now
        if peak > SOUND_LEVEL:
            self.last_sound = now
        self.ring.append((now, np.frombuffer(data, dtype=np.int16).copy()))
        while self.ring and now - self.ring[0][0] > RING_SECONDS:
            self.ring.popleft()
        with self.lock:
            if self.wav is not None:
                expected = int((now - self.t0) * self.rate) - frames
                if expected - self.written > self.rate * 0.3:
                    self._pad(expected)
                self.wav.writeframes(data)
                self.written += frames
        return (None, pyaudio.paContinue)

    def tail(self, seconds):
        """Последние `seconds` секунд как float32, 16 кГц, моно (для распознавания); пусто, если звука не было."""
        now = time.monotonic()
        chunks = [a for t, a in list(self.ring) if now - t <= seconds]
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        mono = np.concatenate(chunks).reshape(-1, self.ch).mean(axis=1) / 32768.0
        n = int(len(mono) * 16000 / self.rate)
        return np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono).astype(np.float32) if n else np.zeros(0, dtype=np.float32)

    def current_level(self):
        """0..1 для индикатора; нет данных дольше 0.3 с (loopback в тишине) — 0."""
        return self.level if time.monotonic() - self.last_cb < 0.3 else 0.0

    def close(self):
        self.stream.stop_stream(), self.stream.close()
        with self.lock:
            self._pad(int((time.monotonic() - self.t0) * self.rate))
            self.wav.close()
            self.wav = None


def silence_warnings(now, started, last_sound_mic, last_sound_sys, mic_limit, sys_limit):
    """Чистая логика: коды предупреждений о тишине. Не раньше чем через limit секунд после начала записи."""
    warns = []
    if now - started > mic_limit and now - last_sound_mic > mic_limit:
        warns.append("mic_silent")
    if now - started > sys_limit and now - last_sound_sys > sys_limit:
        warns.append("system_silent")
    return warns


class Session:
    def __init__(self, folder, meta, manual=False):
        import pyaudiowpatch as pyaudio
        self.folder, self.meta, self.started, self.manual = folder, meta, time.monotonic(), manual
        self.call_key = None  # приложение звонка: когда оно отпустит микрофон — стоп
        self.pending, self.deadline = False, None  # ждём приветствия: пока pending, запись временная
        folder.mkdir(parents=True, exist_ok=True)
        self.pa = pyaudio.PyAudio()
        try:
            wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            mic = self.pa.get_device_info_by_index(wasapi["defaultInputDevice"])
            loop = self.pa.get_default_wasapi_loopback()
            self.mic, self.system = Track(self.pa, mic, folder / "mic.wav"), Track(self.pa, loop, folder / "system.wav")
            self.tracks = [self.mic, self.system]
        except Exception:
            self.pa.terminate()
            raise

    def levels(self):
        return self.mic.current_level(), self.system.current_level()

    def check(self, mic_limit=60, sys_limit=120, devices=False):
        """Коды текущих проблем: mic_silent, system_silent, stream_stopped, (device_changed — только при devices=True)."""
        warns = silence_warnings(time.monotonic(), self.started, self.mic.last_sound, self.system.last_sound, mic_limit, sys_limit)
        if not all(t.stream.is_active() for t in self.tracks):
            warns.append("stream_stopped")
        if devices:
            try:
                if default_device_names() != (self.mic.name, self.system.name):
                    warns.append("device_changed")
            except Exception:
                pass
        return warns

    def stop(self):
        for t in self.tracks:
            try:
                t.close()
            except Exception:
                pass
        self.pa.terminate()
        return time.monotonic() - self.started
