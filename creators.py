"""Creators-server logic. No Discord code in here, so all of it can be tested offline.
Applications, text-message verification, auto-approval, opt-outs, reaching a creator, and the roster."""
import datetime
import hashlib
import hmac
import io
import re
import secrets

import pandas as pd

import core

SCHEMA = """
CREATE TABLE IF NOT EXISTS creator_apps (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id     TEXT NOT NULL,
    kind           TEXT NOT NULL,                       -- artist | model (comes from the button they pressed)
    full_name      TEXT NOT NULL,
    tiktok         TEXT NOT NULL,
    instagram      TEXT NOT NULL,
    email          TEXT NOT NULL,
    phone          TEXT NOT NULL,                       -- E.164, e.g. +13055550100
    phone_verified INTEGER NOT NULL DEFAULT 0,
    code_hash      TEXT,
    code_expires   TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    code_sends     INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'verifying',   -- verifying | review | approved | denied
    decided_by     TEXT,                                -- 'auto' or the staff member's Discord id
    reason         TEXT,
    review_msg_id  TEXT,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_apps_discord ON creator_apps(discord_id);
CREATE TABLE IF NOT EXISTS code_log (phone TEXT NOT NULL, discord_id TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sms_optout (phone TEXT PRIMARY KEY, since TEXT, reason TEXT);

CREATE TABLE IF NOT EXISTS live_stats (
    handle      TEXT PRIMARY KEY,
    last_live   TEXT,           -- YYYY-MM-DD
    week_hours  REAL,
    week_days   REAL,
    hours_30d   REAL,
    days_30d    REAL,
    uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS nudges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    handle      TEXT NOT NULL,
    rule_id     TEXT NOT NULL,
    week        TEXT NOT NULL,                          -- e.g. 2026-W41
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',         -- queued | sent | skipped
    channel     TEXT,
    decided_by  TEXT,
    review_msg_id TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(handle, rule_id, week)                       -- at most one nudge per creator per week per rule
);
CREATE TABLE IF NOT EXISTS nudge_skip (handle TEXT PRIMARY KEY, until TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS lesson_progress (
    discord_id   TEXT NOT NULL,
    lesson_id    TEXT NOT NULL,
    completed_at TEXT,
    passed_at    TEXT,
    PRIMARY KEY (discord_id, lesson_id)
);
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id  TEXT NOT NULL,
    lesson_id   TEXT NOT NULL,
    score_pct   INTEGER NOT NULL,
    passed      INTEGER NOT NULL,
    taken_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS module_awards (discord_id TEXT NOT NULL, module INTEGER NOT NULL, at TEXT, PRIMARY KEY (discord_id, module));
CREATE TABLE IF NOT EXISTS lesson_posts (lesson_id TEXT PRIMARY KEY, channel_id TEXT, thread_id TEXT, message_id TEXT);

CREATE TABLE IF NOT EXISTS studio_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id   TEXT NOT NULL,
    studio_id    TEXT NOT NULL,
    room         TEXT,
    dates        TEXT,
    length       TEXT,
    needs        TEXT,
    status       TEXT NOT NULL DEFAULT 'new',           -- new | emailed | offered | booked | done | not_available | cancelled
    reply_text   TEXT,
    staff_msg_id TEXT,
    email_note   TEXT,
    booked_date  TEXT,                                  -- YYYY-MM-DD
    asked_at     TEXT,
    session_happened INTEGER,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS seen_mail (message_id TEXT PRIMARY KEY, at TEXT);
"""


def ensure_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt):
    return dt.isoformat()


def parse_iso(s):
    return datetime.datetime.fromisoformat(s)


# ---------- cleaning what people type ----------
_TT = re.compile(r"^[a-z0-9._]{2,24}$")
_IG = re.compile(r"^[a-z0-9._]{1,30}$")


def clean_tiktok(v):
    h = core.norm_handle(v)
    if "tiktok.com/" in h:
        h = h.split("tiktok.com/")[-1].strip("/").lstrip("@").split("?")[0]
    if not _TT.match(h):
        raise ValueError("That TikTok username doesn't look right (letters, numbers, . and _ only).")
    return h


def clean_instagram(v):
    h = core.norm_handle(v)
    if "instagram.com/" in h:
        h = h.split("instagram.com/")[-1].strip("/").split("?")[0]
    if not _IG.match(h) or h in core._IG_RESERVED:
        raise ValueError("That Instagram username doesn't look right (letters, numbers, . and _ only).")
    return h


def clean_name(v):
    n = re.sub(r"\s+", " ", str(v or "").strip())
    if len(re.findall(r"[A-Za-zÀ-ɏ]", n)) < 2 or len(n) > 80:
        raise ValueError("Please enter your full name.")
    return n


def to_e164(raw):
    """US/Canada numbers may be typed without +1. Anything else needs the + and country code."""
    raw = str(raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        if not 8 <= len(digits) <= 15:
            raise ValueError("That mobile number doesn't look right. Include the country code, like +44 7700 900123.")
        num = "+" + digits
    elif len(digits) == 10:
        num = "+1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        num = "+" + digits
    else:
        raise ValueError("Please enter your mobile number with the area code, like (305) 555-0100, "
                         "or with a + and country code if it isn't a US number.")
    if num.startswith("+1") and not re.fullmatch(r"\+1[2-9]\d{2}[2-9]\d{6}", num):
        raise ValueError("That US mobile number doesn't look right. Please check the area code and number.")
    return num


def last10(phone):
    return re.sub(r"\D", "", str(phone or ""))[-10:]


def same_phone(a, b):
    return bool(a and b) and last10(a) == last10(b)


def masked(phone):
    d = re.sub(r"\D", "", str(phone or ""))
    return "••••" + d[-4:] if d else "••••"


def _name_tokens(n):
    return re.findall(r"[a-zà-ɏ]+", str(n or "").lower())


def names_match(a, b):
    """Same first and last name (middle names and order of extras don't matter)."""
    x, y = _name_tokens(a), _name_tokens(b)
    return len(x) >= 2 and len(y) >= 2 and x[0] == y[0] and x[-1] == y[-1]


def first_name(full_name, fallback="there"):
    t = str(full_name or "").strip().split()
    return t[0].capitalize() if t else fallback


# ---------- applications ----------
class ApplyBlocked(Exception):
    pass


class CodeLimit(Exception):
    pass


REAPPLY_AFTER_DAYS = 7
CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
MAX_SENDS_PER_APP = 3
MAX_SENDS_PER_PHONE_HOUR = 3
MAX_SENDS_PER_USER_HOUR = 5


def get_app(conn, app_id):
    return conn.execute("SELECT * FROM creator_apps WHERE id=?", (app_id,)).fetchone()


def latest_app(conn, discord_id):
    return conn.execute("SELECT * FROM creator_apps WHERE discord_id=? ORDER BY id DESC LIMIT 1",
                        (str(discord_id),)).fetchone()


def creator_by_discord(conn, discord_id):
    return conn.execute("SELECT * FROM creators WHERE discord_id=?", (str(discord_id),)).fetchone()


def apply_block_reason(conn, discord_id, now=None):
    """Why this person can't apply right now, or None if they can."""
    now = now or utcnow()
    member = creator_by_discord(conn, discord_id)
    if member and member["member_status"] == "member":
        return "You're already a member. Welcome back!"
    last = latest_app(conn, discord_id)
    if last:
        if last["status"] == "approved":
            return "You're already approved. If you can't see the server, message the team."
        if last["status"] == "review":
            return "Your application is already with the team. You'll get a message once it's reviewed."
        if last["status"] == "denied":
            ts = last["updated_at"].replace(" ", "T")
            when = parse_iso(ts if "+" in ts else ts + "+00:00")
            if now - when < datetime.timedelta(days=REAPPLY_AFTER_DAYS):
                return ("We couldn't approve your last application. Please message the team if you think "
                        "that was a mistake.")
    return None


def start_application(conn, discord_id, kind, full_name, tiktok, instagram, email, phone, now=None):
    now = now or utcnow()
    if kind not in ("artist", "model"):
        raise ValueError("Please choose Artist or Model.")
    name, tt, ig = clean_name(full_name), clean_tiktok(tiktok), clean_instagram(instagram)
    mail = core.clean_email(email)
    if not mail:
        raise ValueError("Please enter your email.")
    ph = to_e164(phone)
    reason = apply_block_reason(conn, discord_id, now)
    if reason:
        raise ApplyBlocked(reason)
    conn.execute("DELETE FROM creator_apps WHERE discord_id=? AND status='verifying'", (str(discord_id),))
    cur = conn.execute("INSERT INTO creator_apps(discord_id, kind, full_name, tiktok, instagram, email, phone) "
                       "VALUES (?,?,?,?,?,?,?)", (str(discord_id), kind, name, tt, ig, mail, ph))
    conn.commit()
    return get_app(conn, cur.lastrowid)


def _hash(app_id, code):
    return hashlib.sha256(f"{app_id}:{code}".encode()).hexdigest()


def issue_code(conn, app_id, now=None):
    """Make a new 6-digit code (valid 10 minutes, 5 attempts). Raises CodeLimit if it's being asked for too often."""
    now = now or utcnow()
    app = get_app(conn, app_id)
    if app["code_sends"] >= MAX_SENDS_PER_APP:
        raise CodeLimit("We've sent the maximum number of codes for this application. Please message the team.")
    since = iso(now - datetime.timedelta(hours=1))
    if conn.execute("SELECT COUNT(*) FROM code_log WHERE phone=? AND at>?", (app["phone"], since)).fetchone()[0] >= MAX_SENDS_PER_PHONE_HOUR \
            or conn.execute("SELECT COUNT(*) FROM code_log WHERE discord_id=? AND at>?", (app["discord_id"], since)).fetchone()[0] >= MAX_SENDS_PER_USER_HOUR:
        raise CodeLimit("Too many codes requested. Please wait an hour and try again.")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    conn.execute("UPDATE creator_apps SET code_hash=?, code_expires=?, attempts=0, code_sends=code_sends+1, "
                 "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (_hash(app_id, code), iso(now + datetime.timedelta(minutes=CODE_TTL_MINUTES)), app_id))
    conn.execute("INSERT INTO code_log(phone, discord_id, at) VALUES (?,?,?)", (app["phone"], app["discord_id"], iso(now)))
    conn.commit()
    return code


def check_code(conn, app_id, code, now=None):
    """-> (result, attempts_left). result: ok | bad | expired | locked | none"""
    now = now or utcnow()
    app = get_app(conn, app_id)
    if not app or not app["code_hash"]:
        return "none", 0
    if app["attempts"] >= MAX_ATTEMPTS:
        return "locked", 0
    if now > parse_iso(app["code_expires"]):
        return "expired", 0
    attempts = app["attempts"] + 1
    conn.execute("UPDATE creator_apps SET attempts=? WHERE id=?", (attempts, app_id))
    typed = re.sub(r"\D", "", str(code or ""))
    if hmac.compare_digest(_hash(app_id, typed), app["code_hash"]):
        conn.execute("UPDATE creator_apps SET phone_verified=1, code_hash=NULL, code_expires=NULL, "
                     "updated_at=CURRENT_TIMESTAMP WHERE id=?", (app_id,))
        conn.commit()
        return "ok", MAX_ATTEMPTS - attempts
    conn.commit()
    return ("locked", 0) if attempts >= MAX_ATTEMPTS else ("bad", MAX_ATTEMPTS - attempts)


# ---------- deciding: auto-approve or send to a person ----------
def evaluate(conn, app):
    """Auto-approve only when ALL of these hold:
       the TikTok handle is on the managed-creator list and not terminated, AND at least one more detail matches what we
       have on file (email, name or phone), the phone is verified, the handle isn't linked to a different Discord account,
       and the role agrees with what's on file.
       Returns {'decision': 'auto'|'review', 'problems': [...], 'matched': [...], 'scout_id': ...}"""
    h = app["tiktok"]
    problems, matched = [], []
    managed = conn.execute("SELECT * FROM managed_creators WHERE handle=?", (h,)).fetchone()
    cr = conn.execute("SELECT * FROM creators WHERE handle=?", (h,)).fetchone()
    if managed is None:
        problems.append("TikTok handle isn't on the managed-creator list")
    else:
        if managed["tiktok_status"] == "terminated":
            problems.append("TikTok shows this creator as Terminated")
        emails = {str(x).lower() for x in (managed["email"], cr["email"] if cr else None) if x}
        phones = [x for x in (managed["phone"], cr["phone"] if cr else None) if x]
        names = [x for x in (managed["full_name"], cr["full_name"] if cr else None) if x]
        if app["email"] in emails:
            matched.append("email")
        if any(names_match(app["full_name"], n) for n in names):
            matched.append("name")
        if any(same_phone(app["phone"], p) for p in phones):
            matched.append("phone")
        if not matched:
            problems.append("no email, name or phone on file matches the application")
        kinds = {x for x in (managed["kind"], cr["kind"] if cr else None) if x}
        if kinds and app["kind"] not in kinds:
            problems.append(f"on file as {'/'.join(sorted(kinds))}, but applied as {app['kind']}")
    if cr and cr["discord_id"] and cr["discord_id"] != app["discord_id"]:
        problems.append("this TikTok handle is already linked to a different Discord account")
    mine = conn.execute("SELECT handle FROM creators WHERE discord_id=? AND handle!=?", (app["discord_id"], h)).fetchone()
    if mine:
        problems.append(f"this Discord account is already linked to @{mine['handle']}")
    if not app["phone_verified"]:
        problems.append("mobile number not verified")
    other = conn.execute("SELECT discord_id FROM creator_apps WHERE phone=? AND discord_id!=? AND status IN ('review','approved') LIMIT 1",
                         (app["phone"], app["discord_id"])).fetchone()
    if other:
        problems.append("this mobile number was used on another Discord account's application")
    sub = core.get_submission_by_ig(conn, app["instagram"])
    return {"decision": "review" if problems else "auto", "problems": problems, "matched": matched,
            "scout_id": sub["scout_id"] if sub and sub["decision"] == "approved" else None,
            "referral_status": sub["status"] if sub else None}


def set_app_status(conn, app_id, status, decided_by=None, reason=None):
    conn.execute("UPDATE creator_apps SET status=?, decided_by=?, reason=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (status, decided_by, reason, app_id))
    conn.commit()
    return get_app(conn, app_id)


def register_member(conn, app):
    """Record an approved creator: contact details, Discord link, scout credit (from the Instagram name) and manager."""
    cr = core.upsert_creator(conn, app["tiktok"])
    conn.execute("UPDATE creators SET full_name=?, email=?, phone=?, phone_verified=?, discord_id=?, kind=?, "
                 "member_status='member', approved_at=CURRENT_TIMESTAMP WHERE handle=?",
                 (app["full_name"], app["email"], app["phone"], int(app["phone_verified"]), app["discord_id"],
                  app["kind"], cr["handle"]))
    conn.commit()
    scout_id = None
    try:
        sub = core.link_handles(conn, app["tiktok"], app["instagram"], app["email"], None)
        scout_id = sub["scout_id"]
    except ValueError:
        pass                                    # not a scouted creator: that's fine
    if not conn.execute("SELECT ig_username FROM creators WHERE handle=?", (cr["handle"],)).fetchone()["ig_username"]:
        conn.execute("UPDATE creators SET ig_username=? WHERE handle=?", (app["instagram"], cr["handle"]))
        conn.commit()
    mgr = core.reconcile_by_email(conn, app["email"])
    return {"handle": cr["handle"], "scout_id": scout_id, "manager": mgr[0][1]["manager_name"] if mgr else None}


def set_member_status(conn, handle, status):
    conn.execute("UPDATE creators SET member_status=? WHERE handle=?", (status, core.norm_handle(handle)))
    conn.commit()


def set_checkin_channel(conn, handle, channel_id):
    conn.execute("UPDATE creators SET checkin_channel_id=? WHERE handle=?", (str(channel_id) if channel_id else None,
                                                                              core.norm_handle(handle)))
    conn.commit()


def members(conn):
    return conn.execute("SELECT * FROM creators WHERE member_status='member' ORDER BY handle").fetchall()


# ---------- the roster staff upload (what's "on file") ----------
_ROSTER_ALIASES = {
    "handle": ["handle", "tiktok", "tiktok handle", "tiktok username", "creator's username", "username"],
    "full_name": ["full name", "name", "creator name"],
    "email": ["email", "email address"],
    "phone": ["phone", "mobile", "phone number", "mobile number"],
    "kind": ["role", "type", "kind", "artist or model"],
}


def parse_roster(data: bytes, filename: str):
    name = filename.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    elif name.endswith((".xlsx", ".xlsm")):
        df = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False)
    else:
        raise ValueError("Please upload a .csv or .xlsx file.")
    cols = {str(c).strip().lower(): c for c in df.columns}
    pick = {k: next((cols[a] for a in al if a in cols), None) for k, al in _ROSTER_ALIASES.items()}
    if not pick["handle"]:
        raise ValueError("The roster needs a column called Handle (the TikTok username). "
                         "Optional columns: Full name, Email, Phone, Role.")
    out = []
    for _, r in df.iterrows():
        h = core.norm_handle(r[pick["handle"]])
        if not h:
            continue
        row = {"handle": h}
        for k in ("full_name", "email", "phone", "kind"):
            v = str(r[pick[k]]).strip() if pick[k] else ""
            row[k] = v or None
        out.append(row)
    return out


def import_roster(conn, rows):
    """Add people to the managed-creator list with the details we have on file. Bad values are reported, not saved."""
    rep = {"added": 0, "updated": 0, "bad": []}
    for r in rows:
        try:
            email = core.clean_email(r["email"]) if r["email"] else None
            phone = to_e164(r["phone"]) if r["phone"] else None
        except ValueError as e:
            rep["bad"].append(f"@{r['handle']}: {e}")
            continue
        kind = (r["kind"] or "").strip().lower() or None
        if kind and kind not in ("artist", "model"):
            rep["bad"].append(f"@{r['handle']}: role must be Artist or Model")
            continue
        exists = conn.execute("SELECT 1 FROM managed_creators WHERE handle=?", (r["handle"],)).fetchone()
        if exists:
            conn.execute("UPDATE managed_creators SET full_name=COALESCE(?,full_name), email=COALESCE(?,email), "
                         "phone=COALESCE(?,phone), kind=COALESCE(?,kind) WHERE handle=?",
                         (r["full_name"], email, phone, kind, r["handle"]))
            rep["updated"] += 1
        else:
            conn.execute("INSERT INTO managed_creators(handle, full_name, email, phone, kind) VALUES (?,?,?,?,?)",
                         (r["handle"], r["full_name"], email, phone, kind))
            rep["added"] += 1
    conn.commit()
    return rep


# ---------- text messages: STOP is always honoured ----------
STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
START_WORDS = {"start", "unstop"}
SMS_KINDS = {"account", "checkin"}           # texts are limited to account and check-in messages


class SmsBlocked(Exception):
    """The provider says this number can't be texted (they replied STOP, or the carrier blocked it)."""


def opt_out(conn, phone, reason="stop"):
    conn.execute("INSERT OR REPLACE INTO sms_optout(phone, since, reason) VALUES (?,?,?)",
                 (last10(phone), iso(utcnow()), reason))
    conn.commit()


def opt_in(conn, phone):
    conn.execute("DELETE FROM sms_optout WHERE phone=?", (last10(phone),))
    conn.commit()


def is_opted_out(conn, phone):
    return conn.execute("SELECT 1 FROM sms_optout WHERE phone=?", (last10(phone),)).fetchone() is not None


def classify_inbound(body):
    w = re.sub(r"[^a-z]", "", str(body or "").lower())
    if w in STOP_WORDS:
        return "stop"
    if w in START_WORDS:
        return "start"
    if w == "help":
        return "help"
    return "message"


def creator_by_phone(conn, phone):
    for c in conn.execute("SELECT * FROM creators WHERE phone IS NOT NULL AND member_status IS NOT NULL").fetchall():
        if same_phone(c["phone"], phone):
            return c
    return None


def handle_inbound(conn, from_phone, body, opt_out_type=None):
    """A text arrived. STOP (by words or by the provider's flag) is recorded first and never overridden."""
    kind = classify_inbound(body)
    if str(opt_out_type or "").upper() == "STOP":
        kind = "stop"
    elif str(opt_out_type or "").upper() == "START":
        kind = "start"
    if kind == "stop":
        opt_out(conn, from_phone, "stop reply")
    elif kind == "start":
        opt_in(conn, from_phone)
    return {"kind": kind, "creator": creator_by_phone(conn, from_phone)}


# ---------- reaching a creator ----------
async def contact(conn, creator, text, kind, senders, subject="A message from Hierarchy Music"):
    """One place that reaches a creator through the first channel that works:
    SMS (never if they opted out; only for account and check-in messages), then a Discord DM or their private channel,
    then email, then an alert to staff. Returns the channel used: sms | discord | email | staff."""
    phone = creator["phone"]
    if kind in SMS_KINDS and phone and creator["phone_verified"] and not is_opted_out(conn, phone) and senders.get("sms"):
        try:
            if await senders["sms"](phone, text):
                return "sms"
        except SmsBlocked:
            opt_out(conn, phone, "provider block")
        except Exception:
            pass
    if senders.get("discord"):
        try:
            if await senders["discord"](creator, text):
                return "discord"
        except Exception:
            pass
    if creator["email"] and senders.get("email"):
        try:
            if await senders["email"](creator["email"], subject, text):
                return "email"
        except Exception:
            pass
    if senders.get("alert"):
        await senders["alert"](f"I couldn't reach @{creator['handle']} on any channel. The message was:\n{text}")
    return "staff"
