"""Правила записи и назначение проектов (чистая логика, без UI)."""
import pathlib
import re

from . import settings


def decide(text, cfg):
    """(action, rule_name, project|None); action: record | skip | ask. Первое совпавшее правило побеждает."""
    low = text.lower()
    for rule in cfg["rules"]:
        if any(k.strip() and k.strip().lower() in low for k in rule.get("match", [])):
            return rule["action"], rule.get("name", ""), rule.get("project") or None
    return cfg.get("default_action", "ask"), "", None


def _score(text, keywords):
    low = text.lower()
    return sum(low.count(k.strip().lower()) for k in keywords if k.strip())


def pick_project(text, cfg, min_hits=1, dominance=2):
    """(проект|None, подсказка|None): лидер назначается, если совпадений >= min_hits и он >= dominance раз сильнее
    второго. ponytail: подсчёт ключевых слов, не классификатор."""
    scores = sorted(((_score(text, p.get("keywords", [])), p["name"]) for p in cfg["projects"]), reverse=True)
    if not scores or scores[0][0] == 0:
        return None, None
    best, second = scores[0], (scores[1] if len(scores) > 1 else (0, ""))
    if best[0] >= min_hits and best[0] >= dominance * second[0]:
        return best[1], best[1]
    return None, best[1]


def slug(text, limit=50):
    text = re.sub(r'[\/:*?"<>|\r\n]+', " ", text).strip()
    return re.sub(r"\s+", "_", text)[:limit].strip("_") or "meeting"


def meetings_root(project, cfg):
    """Куда класть встречи: <папка проекта>/Встречи (в англ. интерфейсе — Meetings) или <output>/_Inbox/…"""
    folder = "Встречи" if settings.ui_language(cfg) == "ru" else "Meetings"
    for p in cfg["projects"]:
        if p["name"] == project:
            base = pathlib.Path(p["path"]) if p.get("path") else settings.output_dir(cfg) / slug(p["name"])
            return base / folder
    return settings.output_dir(cfg) / "_Inbox" / folder


def _norm(text):
    return " " + " ".join(re.sub(r"[^\w]+", " ", text.lower().replace("ё", "е")).split()) + " "


def greeting_hit(text, phrases):
    """Первая фраза-приветствие, найденная в тексте (целыми словами, без учёта регистра, ё=е), иначе None."""
    hay = _norm(text)
    for p in phrases:
        n = _norm(p).strip()
        if n and f" {n} " in hay:
            return p
    return None
