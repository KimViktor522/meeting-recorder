"""Обработка записи или импорта: аудио -> транскрипт (локально) -> проект -> протокол со ссылками на фрагменты. Шаги идемпотентны.
  MeetingRecorder --process <папка>   обработать (повторный запуск продолжает с места сбоя)
  MeetingRecorder --retry             дописать протоколы, которые не удались"""
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import time

from . import audio, importer, llm, models, rules, settings

SPEAKER = {"ru": {"mic": "Я", "system": "Собеседник"}, "en": {"mic": "Me", "system": "Other"}}
PROMPTS = {
    "ru": """Ты помощник, составляющий протоколы встреч. Ниже транскрипт встречи (автоматическое распознавание, возможны ошибки в именах и терминах). Строки имеют вид «[мм:сс] Говорящий: текст».
Составь протокол по-русски в Markdown СТРОГО с разделами:
## Участники
## Итоги
## Решения
## Задачи
(таблица: Задача | Ответственный | Срок | Источник; если не названо — «не указан»)
## Открытые вопросы
Правила: ничего не выдумывай; если пункта нет — «нет»; сомнительное помечай «(уточнить)»; без вступления и заключения.
ИСТОЧНИКИ: у каждого пункта в «Решения», «Задачи» и «Открытые вопросы» укажи время фрагмента транскрипта, из которого он взят, в формате [мм:сс] ровно как в начале строки транскрипта (одно-два времени). Не придумывай время и не округляй его.{timing_note}
Дата: {date}. Название: {title}. Проект: {project}. Участники по календарю: {attendees}.""",
    "en": """You write meeting minutes. Below is a meeting transcript (automatic speech recognition, names and terms may be wrong). Lines look like "[mm:ss] Speaker: text".
Write the minutes in English in Markdown with EXACTLY these sections:
## Participants
## Summary
## Decisions
## Action items
(table: Task | Owner | Due | Source; if not stated - "not specified")
## Open questions
Rules: do not invent anything; if a section is empty write "none"; mark doubtful items "(to confirm)"; no introduction or conclusion.
SOURCES: for every item under Decisions, Action items and Open questions give the time of the transcript fragment it comes from, as [mm:ss] exactly as at the start of the transcript line (one or two times). Never invent or round times.{timing_note}
Date: {date}. Title: {title}. Project: {project}. Calendar attendees: {attendees}.""",
}
NO_TIMES = {"ru": "\nВ этом транскрипте НЕТ таймкодов: ссылки [мм:сс] не ставь.", "en": "\nThis transcript has NO timestamps: do not add [mm:ss] references."}
LABELS = {"ru": ("Протокол", "Проект не определён автоматически. Предложение: **{}**. Перенесите папку вручную.", "транскрипт", "аудио", "не определён",
                 "нет источника", "Проверка источников: подтверждено ссылок — {}, снято несуществующих — {}, пунктов без источника — {}."),
          "en": ("Minutes", "Project was not detected. Suggestion: **{}**. Move the folder manually.", "transcript", "audio", "not detected",
                 "no source", "Source check: {} references confirmed, {} nonexistent removed, {} items without a source.")}
REF = re.compile(r"\[(\d{1,3}):(\d{2})\]")
NEEDS_SOURCE = ("реш", "задач", "открыт", "decision", "action", "open")
SNAP = 15  # сек: ссылка, не совпавшая с репликой, но близкая к ней, «прилипает» к ближайшей


def work_root():
    d = settings.data_dir() / "recordings"
    d.mkdir(exist_ok=True)
    return d


def log(msg):
    with open(settings.data_dir() / "process.log", "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


# ---------- звук и распознавание ----------
def prepare_audio(d, meta):
    """Live: mic16/system16 + стерео audio.m4a. Импорт: mono16 из медиа + моно audio.m4a (если есть медиа)."""
    imp = meta.get("import")
    if imp:
        if imp["media"]:
            if not (d / "mono16.wav").exists():
                audio.save_wav(d / "mono16.wav", audio.to_16k_mono(imp["media"]))
            if not (d / "audio.m4a").exists():
                audio.write_m4a(d / "audio.m4a", audio.read_pcm(d / "mono16.wav"))
        return
    for name in ("mic", "system"):
        if not (d / f"{name}16.wav").exists():
            audio.save_wav(d / f"{name}16.wav", audio.to_16k_mono(d / f"{name}.wav"))
    if not (d / "audio.m4a").exists():
        audio.write_m4a(d / "audio.m4a", audio.read_pcm(d / "mic16.wav"), audio.read_pcm(d / "system16.wav"))


def run_whisper(cfg, tracks):
    """tracks: [(wav, подпись|None)] -> сегменты по времени."""
    model = models.load_model(cfg["whisper"]["model"])
    wl = cfg["whisper"].get("language", "auto")
    segs = []
    for wav, who in tracks:
        it, _ = model.transcribe(audio.load_wav(wav), language=None if wl in ("auto", "") else wl, vad_filter=True,
                                 beam_size=1, condition_on_previous_text=False)
        segs += [{"start": round(s.start, 1), "who": who, "text": s.text.strip()} for s in it if s.text.strip()]
    segs.sort(key=lambda s: s["start"])
    return segs


def get_segments(d, cfg, lang, meta):
    out = d / "transcript.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    imp = meta.get("import")
    if imp and imp["transcript"]:
        segs = importer.parse_transcript(d / imp["transcript"])  # готовый транскрипт сервиса — без распознавания
    elif imp:
        segs = run_whisper(cfg, [(d / "mono16.wav", None)])
    else:
        segs = run_whisper(cfg, [(d / "mic16.wav", SPEAKER[lang]["mic"]), (d / "system16.wav", SPEAKER[lang]["system"])])
    out.write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    return segs


def transcript_md(segs):
    lines = []
    for s in segs:
        t = f"[{int(s['start']) // 60:02d}:{int(s['start']) % 60:02d}] " if s.get("start") is not None else ""
        who = f"**{s['who']}:** " if s.get("who") else ""
        lines.append(f"{t}{who}{s['text']}")
    return "\n".join(lines)


# ---------- проверяемый протокол ----------
def transcript_times(md):
    return sorted({int(m) * 60 + int(s) for m, s in re.findall(r"^\[(\d{1,3}):(\d{2})\]", md, re.M)})


def verify_refs(body, times, lang, link_audio=True):
    """Проверяет ссылки [мм:сс] в протоколе по транскрипту: существующие превращает в ссылки на audio.m4a#t=сек, близкие
    «прилипают» к ближайшей реплике, несуществующие снимает; пункты решений/задач/вопросов без источника помечает.
    -> (текст, подтверждено, снято, без_источника)."""
    L = LABELS[lang]
    ok = bad = unsourced = 0

    def fix(m):
        nonlocal ok, bad
        sec = int(m.group(1)) * 60 + int(m.group(2))
        near = min(times, key=lambda t: abs(t - sec)) if times else None
        if near is None or abs(near - sec) > SNAP:
            bad += 1
            return ""
        ok += 1
        label = f"{near // 60:02d}:{near % 60:02d}"
        return f"[{label}](audio.m4a#t={near})" if link_audio else f"[{label}]"

    sep = re.compile(r"\s*\|?[\s:|-]+\|?\s*")
    out, need = [], False
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            need = any(k in line.lower() for k in NEEDS_SOURCE)
            out.append(line)
            continue
        before = ok
        new = REF.sub(fix, line)
        new = re.sub(r"[ \t]{2,}", " ", new) if new != line else new
        stripped = new.strip()
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        is_sep = "-" in new and "|" in new and bool(sep.fullmatch(new))
        is_head = stripped.startswith("|") and "-" in nxt and "|" in nxt and bool(sep.fullmatch(nxt))
        is_item = stripped.startswith(("- ", "* ", "|")) or bool(re.match(r"\d+[.)]\s", stripped))
        if need and is_item and not is_sep and not is_head and ok == before:
            if re.fullmatch(r"([-*]\s*)?(нет|none)\.?", stripped, re.I):
                out.append(new)  # «нет» / «none» — пустой раздел, источника не требует
                continue
            unsourced += 1
            new = (new.rstrip()[:-1].rstrip() + f" ⚠ {L[5]} |") if new.rstrip().endswith("|") else new.rstrip() + f" ⚠ {L[5]}"
        out.append(new)
    text = "\n".join(out).rstrip() + "\n\n> " + L[6].format(ok, bad, unsourced) + "\n"
    return text, ok, bad, unsourced


def write_protocol(d, cfg, lang):
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    md = (d / "transcript.md").read_text(encoding="utf-8")
    times = transcript_times(md)
    L = LABELS[lang]
    instruction = cfg["protocol"].get("prompt") or PROMPTS[lang]
    values = {"date": meta["start"][:10], "title": meta["title"], "project": meta.get("project") or L[4],
              "attendees": ", ".join(a.strip() for a in meta.get("attendees", [])) or "-",
              "timing_note": "" if times else NO_TIMES[lang]}
    for k, v in values.items():  # replace, не format: в пользовательском шаблоне могут быть другие фигурные скобки
        instruction = instruction.replace("{" + k + "}", v)
    body = llm.complete(cfg, instruction, md)
    if times:
        body = verify_refs(body, times, lang, link_audio=(d / "audio.m4a").exists())[0]
    hint = f"\n> {L[1].format(meta['hint'])}\n" if not meta.get("project") and meta.get("hint") else ""
    links = f"[{L[2]}](transcript.md)" + (f" · [{L[3]}](audio.m4a)" if (d / "audio.m4a").exists() else "")
    head = (f"---\ndate: {meta['start'][:10]}\ntitle: {meta['title']}\nproject: {meta.get('project') or L[4]}\n"
            f"duration_min: {round(meta.get('seconds', 0) / 60)}\n---\n# {L[0]}: {meta['title']}\n{hint}\n{links}\n\n")
    (d / "protocol.md").write_text(head + body.rstrip() + "\n", encoding="utf-8")
    log(f"PROTOCOL ok {d}")


# ---------- основной конвейер ----------
def process(work, cfg):
    lang = settings.ui_language(cfg)
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    prepare_audio(work, meta)
    segs = get_segments(work, cfg, lang, meta)
    md = transcript_md(segs) or "_No speech recognized._"
    (work / "transcript.md").write_text(md + "\n", encoding="utf-8")
    ctx = " ".join([meta["title"], *meta.get("attendees", []), *meta.get("titles", [])])
    project, hint = meta.get("project_rule"), None
    if not project:
        project, hint = rules.pick_project(ctx, cfg)  # календарь/окна/название — сильный сигнал
    if not project:
        project, hint2 = rules.pick_project(ctx + " " + md, cfg, min_hits=3)  # содержание — только при явном перевесе
        hint = hint or hint2
    meta.update(project=project, hint=hint)
    base = rules.meetings_root(project, cfg) / f"{meta['start'][:10]}_{rules.slug(meta['title'])}"
    final, n = base, 1
    while final.exists():
        n += 1
        final = base.with_name(f"{base.name}_{n}")
    final.parent.mkdir(parents=True, exist_ok=True)
    imp = meta.get("import")
    (work / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.move(str(work), str(final))
    for junk in ("mic16.wav", "system16.wav", "mic.wav", "system.wav", "mono16.wav", "transcript.json"):
        (final / junk).unlink(missing_ok=True)  # сырьё удаляем, только когда audio.m4a и транскрипт уже на месте
    if imp and imp.get("media") and imp.get("keep_original"):
        src = pathlib.Path(imp["media"])
        shutil.copy2(src, final / ("source" + src.suffix.lower()))
    log(f"DONE {final} project={project} hint={hint}")
    if cfg["protocol"]["provider"] != "none":
        try:
            write_protocol(final, cfg, lang)
        except Exception as e:
            log(f"PROTOCOL FAIL {final}: {e!r}")
            with open(work_root() / "pending_protocols.txt", "a", encoding="utf-8") as f:
                f.write(str(final) + "\n")
    return final


def retry(cfg):
    pending = work_root() / "pending_protocols.txt"
    if not pending.exists():
        return
    lang, left = settings.ui_language(cfg), []
    for f in dict.fromkeys(pathlib.Path(x) for x in pending.read_text(encoding="utf-8").splitlines() if x.strip()):
        try:
            if (f / "transcript.md").exists() and not (f / "protocol.md").exists():
                write_protocol(f, cfg, lang)
        except Exception as e:
            log(f"RETRY FAIL {f}: {e!r}")
            left.append(str(f))
    pending.write_text("\n".join(left) + ("\n" if left else ""), encoding="utf-8")


def run_locked(fn, *args):
    """Одна тяжёлая обработка за раз (файл-замок; зависший >3 ч считается мёртвым). ponytail: опрос раз в 10 с."""
    lock = work_root() / ".lock"
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL))
            break
        except FileExistsError:
            if time.time() - lock.stat().st_mtime > 3 * 3600:
                lock.unlink(missing_ok=True)
            time.sleep(10)
    try:
        return fn(*args)
    except Exception as e:
        log(f"FAIL {args!r}: {e!r}")  # сырые файлы остались — повтор: --process <папка>
        raise
    finally:
        lock.unlink(missing_ok=True)
