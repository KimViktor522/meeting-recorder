# Meeting Recorder

**Automatic audio recording of online meetings for Windows — with local transcription, project sorting and AI-written minutes.**
[Русская версия](README.ru.md)

Works with any call app that runs on your PC (Zoom, Google Meet, Microsoft Teams, Yandex Telemost, Bitrix24, ControlTalk, Discord…): it records your microphone and the system sound, so there is nothing to integrate. No video is recorded.

## Features
- **Auto-detects calls** (microphone in use + a call app window) — nothing to click.
- **Recording rules**: *record*, *skip* or *ask* by keywords in the calendar event title, attendees or window title. Skip meetings that the service already records (e.g. classes, customer calls); record those nobody records (daily stand-ups).
- **Local transcription** with [faster-whisper](https://github.com/SYSTRAN/faster-whisper): audio never leaves your PC. Choose the model (tiny … large-v3) in the settings.
- **Projects**: meetings are filed into project folders by rule, calendar title, or words in the conversation; unclear ones go to `_Inbox`.
- **Minutes**: sections *Participants / Summary / Decisions / Action items / Open questions* via any OpenAI-compatible API (OpenAI, Z.ai, OpenRouter, your own), the Anthropic API, a local **Ollama**, or Claude Code CLI. Or turn it off and keep transcripts only.
- **Calendar** through a secret ICS link (Google, Yandex, Outlook) — no OAuth.
- Tray icon, settings window (English / Russian), start with Windows, API keys encrypted with Windows DPAPI.

## Install
**Installer:** download `MeetingRecorder-Setup.exe` from the [Releases](../../releases) page and run it.
**From source** (Python 3.10+, Windows):
```powershell
git clone https://github.com/KimViktor522/meeting-recorder; cd meeting-recorder
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py
```

## First run
1. The settings window opens. Add **projects** and **recording rules**.
2. **Transcription** tab: pick a model and press *Download* (small ≈ 485 MB is a good start for a laptop without GPU).
3. *(Optional)* **Minutes** tab: choose a provider, paste an API key, press *Test*. **Calendar** tab: paste your ICS link.
4. Close the window — the app keeps running in the tray. Click the icon to open settings again.

Results appear in `<meetings folder>\<project>\Meetings\<date>_<title>\`: `audio.m4a` (left channel = you, right = others), `transcript.md`, `protocol.md`.

## How a call is detected
A call is recorded only when **the same application** holds the microphone **and** owns a window whose title contains a name from *Call apps* (checked by the window's process `exe`). A folder named "Telemost" in Explorer while VS Code uses the microphone is *not* a call.
To see what the detector sees during a real call: tray → *Status & diagnostics* → *What the detector sees*, or run `MeetingRecorder.exe --diagnose` (writes `diagnose.log` in the data folder). If your service is not detected, add a word from its window title to *Call apps*.

## Start on a greeting
Optional (*Greeting* tab). After a call is detected the app listens locally with a small model and keeps nothing until it hears a phrase such as "good morning" or "здравствуйте". Then recording is kept from the start of the call; if the call ends without a greeting, the temporary audio is deleted. If no greeting is heard for N minutes, recording starts anyway (0 = never). The phrase list is editable.

## Recording diagnostics
Tray → **Status & diagnostics**: live level bars for the microphone and system audio, buttons **Start recording / Stop** (manual recordings never auto-stop), and an event log. During any recording you get a tray notification if the microphone is silent for 1 min, system audio is silent for 2 min, the capture stream stops, or the default audio device changes.

## Verifiable minutes
Every decision, action item and open question carries the time of the transcript fragment it came from: `[12:34]`, a link to `audio.m4a#t=754`. The code checks each reference against the transcript: nonexistent ones are removed, items without a source are marked `⚠ no source`, and a summary line is added at the end.

## Import a ready recording or transcript
Tray → **Import a meeting…** (or `MeetingRecorder.exe --import call.mp4 --transcript call.vtt --title "Sync" --project Alpha`). Accepts video/audio (audio is extracted; video is not kept unless you tick *keep a copy*) and/or a ready transcript (VTT, SRT, TXT, MD, with or without timestamps and speaker names). With a ready transcript no speech recognition runs. It then goes through the same *project → minutes* pipeline — handy for meetings your rules skip because the service records them itself.

## Privacy and legal
- Audio and transcripts stay on your PC. **Only the transcript text** is sent to the minutes provider you choose (use Ollama to keep everything local).
- **Recording conversations may require consent of the participants depending on your country. Tell people they are being recorded. You are responsible for complying with the law.**
- Data folder: `%APPDATA%\MeetingRecorder` (config, logs). API keys and the ICS link are stored encrypted for your Windows user.

## Limitations
- Windows 10/11 only; default audio devices are used. Headphones are recommended (otherwise the microphone also hears the speakers).
- Speakers are separated only as *Me / Other*, not by person.
- Detection relies on the call window title containing a name from the *Call apps* list (General tab); add yours if needed.

## Develop
```powershell
python tests\test_all.py            # tests
powershell -File build.ps1          # MeetingRecorder.exe + installer (needs Inno Setup 6)
```
Structure: `meeting_recorder/` — `app.py` (tray, main loop), `capture.py` (detect, record), `process.py` (pipeline), `ui.py` (settings), `rules.py`, `ics.py`, `llm.py`, `models.py`, `settings.py`, `i18n.py`.

## License
MIT — see [LICENSE](LICENSE).
