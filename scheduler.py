"""Intro-call scheduling. A creator gives an email, a time and a time zone; we find a manager whose Google
Calendar is open, create the invite, and record the booking (which later links the manager to the creator).
No Discord code in here, so it can be tested alone."""
import datetime as dt
import re
import uuid
from zoneinfo import ZoneInfo

import core

SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.freebusy"]

TZ_ALIASES = {
    "est": "America/New_York", "edt": "America/New_York", "et": "America/New_York", "eastern": "America/New_York",
    "cst": "America/Chicago", "cdt": "America/Chicago", "ct": "America/Chicago", "central": "America/Chicago",
    "mst": "America/Denver", "mdt": "America/Denver", "mt": "America/Denver", "mountain": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles", "pt": "America/Los_Angeles", "pacific": "America/Los_Angeles",
    "akst": "America/Anchorage", "akdt": "America/Anchorage", "hst": "Pacific/Honolulu",
    "utc": "UTC", "gmt": "UTC", "z": "UTC", "bst": "Europe/London", "cet": "Europe/Paris", "cest": "Europe/Paris",
}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class NoSlot(Exception):
    def __init__(self, suggestions):
        self.suggestions = suggestions


class AlreadyBooked(Exception):
    def __init__(self, booking):
        self.booking = booking


class NotConnected(Exception):
    pass


# ---------- reading what a creator typed ----------
def parse_tz(text):
    t = str(text or "").strip()
    if not t:
        raise ValueError("Please include the creator's time zone, like EST, PST, or America/New_York.")
    key = re.sub(r"\s*time$", "", t.lower().replace(".", "")).strip()
    if key in TZ_ALIASES:
        return ZoneInfo(TZ_ALIASES[key])
    m = re.fullmatch(r"(?:utc|gmt)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", key)
    if m:
        delta = dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return dt.timezone(delta if m.group(1) == "+" else -delta)
    for candidate in (t, t.replace(" ", "_")):
        try:
            return ZoneInfo(candidate)
        except Exception:
            pass
    raise ValueError(f"I don't recognise the time zone '{t}'. Use EST, PST, UTC+2, or a name like America/New_York.")


def parse_date(text, today):
    t = str(text or "").strip().lower().replace(",", " ")
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    if t in ("today",):
        return today
    if t in ("tomorrow", "tmrw"):
        return today + dt.timedelta(days=1)
    for i, w in enumerate(WEEKDAYS):                               # "thursday" -> the next Thursday
        if t.startswith(w) and len(t) <= 9:
            return today + dt.timedelta(days=(i - today.weekday()) % 7 or 7)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t)
    if m:
        year = int(m[3]) + (2000 if m[3] and len(m[3]) == 2 else 0) if m[3] else today.year
        d = dt.date(year, int(m[1]), int(m[2]))
        return d if (m[3] or d >= today) else d.replace(year=year + 1)
    m = (re.fullmatch(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:\s+(\d{4}))?", t)
         or None)
    if m and m[1] in MONTHS:
        d = dt.date(int(m[3]) if m[3] else today.year, MONTHS[m[1]], int(m[2]))
        return d if (m[3] or d >= today) else d.replace(year=d.year + 1)
    m = re.fullmatch(r"(\d{1,2})\s+([a-z]{3})[a-z]*\.?(?:\s+(\d{4}))?", t)
    if m and m[2] in MONTHS:
        d = dt.date(int(m[3]) if m[3] else today.year, MONTHS[m[2]], int(m[1]))
        return d if (m[3] or d >= today) else d.replace(year=d.year + 1)
    raise ValueError("I couldn't read that date. Try 2026-10-08, 10/8, Oct 8, or a day like Thursday.")


def parse_time(text):
    t = str(text or "").strip().lower().replace(".", "")
    if t == "noon":
        return dt.time(12, 0)
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?", t)
    if not m:
        raise ValueError("I couldn't read that time. Try 3pm, 3:30 PM, or 15:00.")
    hour, minute, ap = int(m[1]), int(m[2] or 0), m[3]
    if minute > 59:
        raise ValueError("That time has invalid minutes.")
    if ap:
        if not 1 <= hour <= 12:
            raise ValueError("That time doesn't look right.")
        hour = hour % 12 + (12 if ap.startswith("p") else 0)
    elif not (hour >= 13 or hour == 0 or (m[2] and len(m[1]) == 2 and m[1].startswith("0"))):
        raise ValueError("Please include AM or PM, or use 24-hour time like 15:00.")
    if hour > 23:
        raise ValueError("That time doesn't look right.")
    return dt.time(hour, minute)


def parse_when(date_text, time_text, tz_text, now=None):
    """-> timezone-aware datetime in the creator's own time zone."""
    tz = parse_tz(tz_text)
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    return dt.datetime.combine(parse_date(date_text, now.date()), parse_time(time_text), tzinfo=tz)


def parse_iso(text, tz_text=None):
    s = str(text).strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        raise ValueError("Couldn't read that date/time. Use a format like 2026-10-08T15:00:00-04:00.")
    if d.tzinfo is None:
        d = d.replace(tzinfo=parse_tz(tz_text))
    return d


# ---------- availability ----------
def _settings(conn):
    return (int(float(core.get_setting(conn, "work_start", 9))), int(float(core.get_setting(conn, "work_end", 18))),
            int(float(core.get_setting(conn, "call_minutes", 30))))


def within_hours(start, minutes, org_tz, work_start, work_end):
    lo = start.astimezone(org_tz)
    hi = lo + dt.timedelta(minutes=minutes)
    return (lo.weekday() < 5 and hi.date() == lo.date() and lo.hour >= work_start
            and (hi.hour + hi.minute / 60) <= work_end)


def _is_free(busy, email, start, end):
    spans = busy.get(email)
    if spans is None:       # calendar not shared with us -> don't guess
        return False
    return all(end <= s or start >= e for s, e in spans)


def _round_robin(conn, free):
    rows = {r["email"]: r for r in conn.execute("SELECT * FROM managers WHERE active=1")}
    return sorted(free, key=lambda e: (rows[e]["last_assigned"] or "", e))[0]


def schedule_call(conn, cal, org_tz, email, name, start, now=None, ig_username=None):
    """Book an intro call at `start` (aware datetime). Returns {'booking', 'manager_name', 'start', 'meet_link'}."""
    email = core.clean_email(email)
    if not email:
        raise ValueError("The creator's email is required.")
    now = now or dt.datetime.now(dt.timezone.utc)
    work_start, work_end, minutes = _settings(conn)
    start = start.astimezone(dt.timezone.utc)
    if start < now + dt.timedelta(hours=1):
        raise ValueError("That time is in the past, or less than an hour away. Please pick a later time.")
    existing = core.latest_booking(conn, email)
    if existing:
        raise AlreadyBooked(existing)
    managers = [r for r in conn.execute("SELECT * FROM managers WHERE active=1")]
    if not managers:
        raise ValueError("No managers are set up yet. Add one with /addmanager.")
    emails = [m["email"] for m in managers]
    busy = cal.busy(emails, start, start + dt.timedelta(days=8))
    end = start + dt.timedelta(minutes=minutes)
    free = [e for e in emails if _is_free(busy, e, start, end)] if within_hours(start, minutes, org_tz, work_start, work_end) else []
    if not free:                                      # offer the next three times that do work
        out, probe = [], start
        while len(out) < 3 and probe < start + dt.timedelta(days=7):
            probe += dt.timedelta(minutes=30)
            pe = probe + dt.timedelta(minutes=minutes)
            ok = probe >= now + dt.timedelta(hours=1)
            ok = ok and within_hours(probe, minutes, org_tz, work_start, work_end)
            if ok and any(_is_free(busy, e, probe, pe) for e in emails):
                out.append(probe)
        raise NoSlot(out)
    chosen = next(m for m in managers if m["email"] == _round_robin(conn, free))
    ev = cal.create_event(f"Hierarchy intro call: {name or email}", start, end, [email, chosen["email"]],
                          f"Intro call with {chosen['name'] or chosen['email']} from Hierarchy Music.")
    core.record_booking(conn, ev["id"], email, name, chosen["email"], chosen["name"], start.isoformat())
    conn.execute("UPDATE managers SET last_assigned=? WHERE email=?", (now.isoformat(), chosen["email"]))
    conn.commit()
    return {"booking": core.latest_booking(conn, email), "manager_name": chosen["name"] or chosen["email"],
            "start": start, "meet_link": ev.get("meet_link")}


def cancel_call(conn, cal, email):
    b = core.latest_booking(conn, core.clean_email(email))
    if b is None:
        raise ValueError("No active call found for that email.")
    try:
        cal.delete_event(b["uid"])
    except Exception:
        pass  # already deleted on the calendar side
    core.cancel_booking(conn, b["uid"])
    return b


# ---------- managers ----------
def add_manager(conn, email, name):
    email = core.clean_email(email)
    if not email:
        raise ValueError("Manager email is required.")
    conn.execute("INSERT INTO managers(email, name, active) VALUES (?,?,1) "
                 "ON CONFLICT(email) DO UPDATE SET name=excluded.name, active=1", (email, (name or "").strip() or None))
    conn.commit()


def remove_manager(conn, email):
    cur = conn.execute("UPDATE managers SET active=0 WHERE email=?", (core.clean_email(email),))
    conn.commit()
    return cur.rowcount > 0


# ---------- Google Calendar ----------
def _dt(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


class GoogleCalendar:
    """Uses one authorised account as the organiser: it reads each manager's free/busy (they share their calendar
    with it) and creates the events, so Google emails the invite to the creator and the manager."""

    def __init__(self, client_id, client_secret, refresh_token):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials(None, refresh_token=refresh_token, token_uri="https://oauth2.googleapis.com/token",
                            client_id=client_id, client_secret=client_secret, scopes=SCOPES)
        self.service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def busy(self, emails, start, end):
        res = self.service.freebusy().query(body={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": e} for e in emails]}).execute()
        out = {}
        for e in emails:
            cal = (res.get("calendars") or {}).get(e) or {}
            out[e] = None if (cal.get("errors") or e not in (res.get("calendars") or {})) else \
                [(_dt(b["start"]), _dt(b["end"])) for b in cal.get("busy", [])]
        return out

    def create_event(self, summary, start, end, attendees, description=""):
        body = {"summary": summary, "description": description,
                "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
                "attendees": [{"email": a} for a in attendees],
                "conferenceData": {"createRequest": {"requestId": uuid.uuid4().hex,
                                                     "conferenceSolutionKey": {"type": "hangoutsMeet"}}}}
        ev = self.service.events().insert(calendarId="primary", body=body, sendUpdates="all",
                                          conferenceDataVersion=1).execute()
        return {"id": ev["id"], "meet_link": ev.get("hangoutLink")}

    def delete_event(self, event_id):
        self.service.events().delete(calendarId="primary", eventId=event_id, sendUpdates="all").execute()
