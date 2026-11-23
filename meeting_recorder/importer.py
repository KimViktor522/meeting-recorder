"""Импорт готового материала встречи (видео, аудио, транскрипт) в тот же конвейер «проект -> протокол».
Транскрипты: VTT, SRT, TXT, MD (с таймкодами и именами говорящих или без них)."""
import datetime as dt
import json
import pathlib
import re
import shutil

from . import audio, rules, settings

MEDIA_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".flac", ".wma"}
TRANSCRIPT_EXT = {".vtt", ".srt", ".txt", ".md"}
_T = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,]\d{1,3})?"
_CUE = re.compile(_T + r"\s*-->")
_LINE_TS = re.compile(r"^\s*[\[(]?" + _T + r"[\])]?\s*[-–—]?\s*(.*)$")
_HEADER = re.compile(r"^\s*(\S.{0,38}?)\s+[\[(]?" + _T + r"[\])]?\s*$")  # «Иван Петров 00:01:12» — заголовок реплики
_SPEAKER = re.compile(r"^([A-ZА-ЯЁ][^:\[\]<>]{0,38}):\s+(.+)$")
_VTAG = re.compile(r"<v\s+([^>]+)>")


def _sec(h, m, s):
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def _clean(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def _split_speaker(text):
    m = _SPEAKER.match(text)
    return (m.group(1).strip(), m.group(2).strip()) if m and len(m.group(1).split()) <= 4 else (None, text)


def _parse_cues(raw):
    segs = []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n")):
        lines = [l for l in block.split("\n") if l.strip()]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        m = _CUE.search(lines[idx])
        body = " ".join(lines[idx + 1:])
        who = None
        v = _VTAG.search(body)
        if v:
            who = v.group(1).strip()
        text = _clean(body)
        if not who:
            who, text = _split_speaker(text)
        if m and text:
            segs.append({"start": float(_sec(*m.groups())), "who": who, "text": text})
    return segs


def _parse_text(raw):
    segs, cur = [], None
    for line in raw.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            cur = None
            continue
        h = _HEADER.match(line)
        if h and not _LINE_TS.match(line):  # «Имя 00:12» отдельной строкой, текст — на следующих
            cur = {"start": float(_sec(*h.groups()[1:])), "who": h.group(1).strip(), "text": ""}
            segs.append(cur)
            continue
        t = _LINE_TS.match(line)
        if t:
            who, text = _split_speaker(t.group(4).strip())
            if text:
                segs.append({"start": float(_sec(*t.groups()[:3])), "who": who, "text": text})
            cur = None
        elif cur is not None:
            cur["text"] = (cur["text"] + " " + line.strip()).strip()
        else:
            who, text = _split_speaker(line.strip())
            segs.append({"start": None, "who": who, "text": text})
    return [s for s in segs if s["text"]]


def parse_transcript(path):
    """-> [{'start': сек|None, 'who': имя|None, 'text': str}]. Бросает ValueError, если ничего не нашли."""
    p = pathlib.Path(path)
    raw = p.read_text(encoding="utf-8-sig", errors="replace")
    segs = _parse_cues(raw) if p.suffix.lower() in (".vtt", ".srt") or "-->" in raw[:2000] else _parse_text(raw)
    if not segs:
        raise ValueError(f"no transcript text found in {p.name}")
    if any(s["start"] is not None for s in segs):  # смесь: untimed-строки без времени получают время предыдущей
        last = 0.0
        for s in segs:
            s["start"] = last if s["start"] is None else s["start"]
            last = s["start"]
    return segs


def import_meeting(cfg, media=None, transcript=None, title=None, date=None, project=None, keep_original=False):
    """Готовит рабочую папку для process.py и возвращает её путь. Исходные файлы не меняются и не удаляются."""
    if not media and not transcript:
        raise ValueError("nothing to import")
    media = pathlib.Path(media).resolve() if media else None
    transcript = pathlib.Path(transcript).resolve() if transcript else None
    for f in (media, transcript):
        if f and not f.is_file():
            raise FileNotFoundError(str(f))
    if media and media.suffix.lower() not in MEDIA_EXT:
        raise ValueError(f"unsupported media type: {media.suffix}")
    if transcript:
        parse_transcript(transcript)  # проверяем сразу, а не после очереди
    src = media or transcript
    title = (title or src.stem).strip()
    when = dt.datetime.fromisoformat(date) if date else dt.datetime.fromtimestamp(src.stat().st_mtime)
    seconds = None
    if media:
        try:
            seconds = round(audio.duration_seconds(media) or 0) or None
        except Exception:
            pass
    root = settings.data_dir() / "recordings"
    root.mkdir(exist_ok=True)
    work = root / f"{when:%Y-%m-%d_%H-%M}_{rules.slug(title)}_import"
    n = 1
    while work.exists():
        n += 1
        work = work.with_name(f"{when:%Y-%m-%d_%H-%M}_{rules.slug(title)}_import{n}")
    work.mkdir()
    if transcript:
        shutil.copy2(transcript, work / ("imported_transcript" + transcript.suffix.lower()))
    meta = {"title": title, "attendees": [], "titles": [], "project_rule": project or None, "start": when.isoformat(timespec="seconds"),
            "seconds": seconds or 0, "import": {"media": str(media) if media else None,
                                                "transcript": "imported_transcript" + transcript.suffix.lower() if transcript else None,
                                                "keep_original": bool(keep_original)}}
    (work / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")  # meta последним: маркер готовности
    return work
