"""Событие календаря по секретной ICS-ссылке (Google, Яндекс, Outlook), с повторяющимися событиями."""
import datetime as dt
import time
import urllib.request

_cache = {}  # url -> (время загрузки, текст); ponytail: кэш 5 минут в памяти


def fetch(url, ttl=300):
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("ICS URL must start with https://")
    req = urllib.request.Request(url, headers={"User-Agent": "MeetingRecorder"})
    text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    _cache[url] = (time.time(), text)
    return text


def current_event(ics_text, now=None, window_min=15):
    """Идущее сейчас (или начинающееся в ближайшие window_min минут) событие: {title, attendees} или None."""
    import icalendar
    import recurring_ical_events
    now = now or dt.datetime.now(dt.timezone.utc)
    cal = icalendar.Calendar.from_ical(ics_text)
    best = None
    for ev in recurring_ical_events.of(cal).between(now - dt.timedelta(hours=12), now + dt.timedelta(minutes=window_min)):
        start, end = ev.get("DTSTART").dt, ev.get("DTEND").dt if ev.get("DTEND") else None
        if not isinstance(start, dt.datetime):
            continue  # события на весь день — не встречи
        if start.tzinfo is None:
            start = start.astimezone()
        end = end or start + dt.timedelta(hours=1)
        if end.tzinfo is None:
            end = end.astimezone()
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if start - dt.timedelta(minutes=window_min) <= now <= end and (best is None or start > best[0]):
            attendees = ev.get("ATTENDEE", [])
            attendees = attendees if isinstance(attendees, list) else [attendees]
            names = [str(a.params.get("CN", "")) + " " + str(a).replace("mailto:", "") for a in attendees]
            best = (start, {"title": str(ev.get("SUMMARY", "")), "attendees": names})
    return best[1] if best else None


def event_now(cfg):
    """Для приложения: None при выключенном календаре и при любой ошибке (календарь не должен ронять запись)."""
    from . import settings
    try:
        c = cfg["calendar"]
        url = settings.unprotect(c.get("ics_url_enc", ""))
        return current_event(fetch(url)) if c.get("enabled") and url else None
    except Exception:
        return None
