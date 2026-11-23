# Changelog

## 1.2.0
- Optional "start on a greeting": the call is listened to locally and kept only after a greeting phrase is heard (editable list, fallback timer).

## 1.1.0
- Call detection now requires the window to belong to the same process that uses the microphone (fixes false starts); `--diagnose` mode.
- Recording diagnostics: live level bars, silence / stream / device-change warnings, manual Start and Stop.
- Minutes cite transcript timestamps; references are verified by code, unsourced items are flagged.
- Import of ready video/audio/transcript (VTT, SRT, TXT, MD) from the tray or `--import`.

## 1.0.0
- First public version: automatic call detection, rule-based recording (record / skip / ask), local transcription (faster-whisper), project assignment, meeting minutes via OpenAI-compatible APIs, Anthropic API or Claude Code CLI.
- Settings window (RU/EN), tray icon, ICS calendar support, encrypted API keys (DPAPI), Windows installer.
