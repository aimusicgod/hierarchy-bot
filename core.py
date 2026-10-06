"""Ledger + TikTok LIVE export importer. No Discord code in here, so it can be tested alone."""
import io
import re
import sqlite3
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

SCOUT_PCT = Decimal("0.05")
MANAGER_PCT = Decimal("0.30")
OWNER = "OWNER"  # owner gets the remainder (65%), plus any share with no assigned person

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    handle     TEXT PRIMARY KEY,
    scout_id   TEXT,
    manager_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ledger (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    month        TEXT NOT NULL,              -- YYYYMM, e.g. 202608
    handle       TEXT NOT NULL,
    role         TEXT NOT NULL,              -- scout | manager | owner
    payee_id     TEXT NOT NULL,              -- Discord user id, or OWNER
    amount_cents INTEGER NOT NULL,           -- this person's cut
    gross_cents  INTEGER NOT NULL,           -- the creator's bonus this row came from
    kind         TEXT NOT NULL DEFAULT 'import',   -- import | adjustment
    note         TEXT,
    paid         INTEGER NOT NULL DEFAULT 0,
    paid_at      TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
-- one imported row per creator/month/role: re-importing a file can never double-pay
CREATE UNIQUE INDEX IF NOT EXISTS uq_import ON ledger(month, handle, role) WHERE kind = 'import';
CREATE INDEX IF NOT EXISTS ix_payee ON ledger(payee_id, month);

-- monthly activity from the TikTok export, used for benchmark check-ins
CREATE TABLE IF NOT EXISTS creator_stats (
    month      TEXT NOT NULL,
    handle     TEXT NOT NULL,
    diamonds   INTEGER,
    valid_days REAL,
    hours      REAL,
    PRIMARY KEY (month, handle)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

-- every creator in TikTok's "Manage creators" export (the managed-creator list), refreshed on each import
CREATE TABLE IF NOT EXISTS managed_creators (
    handle         TEXT PRIMARY KEY,
    tiktok_status  TEXT,                 -- pending | effective | terminated, straight from TikTok
    manager_email  TEXT,
    last_live      TEXT,
    full_name      TEXT,                 -- the next four come from the roster you upload (they are the "on file" details)
    email          TEXT,
    phone          TEXT,
    kind           TEXT,                 -- artist | model
    seen_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

-- everyone who gets paid (scouts by Discord id, managers by name key) and whether their W-9 is on file
CREATE TABLE IF NOT EXISTS payees (
    payee_id    TEXT PRIMARY KEY,
    name        TEXT,
    w9_on_file  INTEGER NOT NULL DEFAULT 0,
    w9_date     TEXT
);

CREATE TABLE IF NOT EXISTS w9_events (
    envelope_id  TEXT PRIMARY KEY,           -- the DocuSign envelope: the audit trail back to the signed form
    signer_name  TEXT,
    signer_email TEXT,
    scout_id     TEXT,                       -- NULL until it is linked to a scout
    received_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- a creator's intro call, booked through the scheduling tool; the host is the manager who had that time open
CREATE TABLE IF NOT EXISTS bookings (
    uid           TEXT PRIMARY KEY,
    invitee_email TEXT NOT NULL,
    invitee_name  TEXT,
    manager_email TEXT,
    manager_name  TEXT,
    start_time    TEXT,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | canceled | superseded
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_booking_email ON bookings(invitee_email);

-- managers whose calendars are checked when a creator asks for an intro call
CREATE TABLE IF NOT EXISTS managers (
    email         TEXT PRIMARY KEY,
    name          TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    last_assigned TEXT
);

-- people who applied to be scouts (full name, email, phone stay admin-only)
CREATE TABLE IF NOT EXISTS scouts (
    discord_id    TEXT PRIMARY KEY,
    full_name     TEXT,
    email         TEXT,
    phone         TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending / approved / denied
    review_msg_id TEXT,
    agreement_version TEXT,
    agreed_at     TEXT,
    agreement_text TEXT,
    forfeited     INTEGER NOT NULL DEFAULT 0,   -- 1 = removed for breaking the agreement: no commission for later months
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ig_username TEXT NOT NULL UNIQUE,        -- first scout to submit gets credit
    ig_url      TEXT NOT NULL,
    scout_id    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, col, ddl in [("creators", "ig_username", "TEXT"), ("submissions", "review_msg_id", "TEXT"),
                          ("submissions", "decision", "TEXT NOT NULL DEFAULT 'review'"),
                          ("scouts", "agreement_version", "TEXT"), ("scouts", "agreed_at", "TEXT"), ("scouts", "agreement_text", "TEXT"), ("scouts", "forfeited", "INTEGER NOT NULL DEFAULT 0"), ("submissions", "exported_at", "TEXT"),
                          ("payees", "w9_envelope_id", "TEXT"),
                          ("creators", "email", "TEXT"), ("creators", "phone", "TEXT"), ("creators", "discord_id", "TEXT"),
                          ("creators", "full_name", "TEXT"), ("creators", "kind", "TEXT"),
                          ("creators", "phone_verified", "INTEGER NOT NULL DEFAULT 0"),
                          ("creators", "member_status", "TEXT"), ("creators", "checkin_channel_id", "TEXT"),
                          ("creators", "approved_at", "TEXT"), ("creators", "weekly_goal_hours", "REAL")]:
        if col not in [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    conn.execute("UPDATE submissions SET status = CASE status WHEN 'signed' THEN 'effective' "
                 "WHEN 'declined' THEN 'terminated' WHEN 'review' THEN 'pending' WHEN 'submitted' THEN 'pending' "
                 "WHEN 'contacted' THEN 'pending' WHEN 'onboarding' THEN 'pending' ELSE status END")
    # scouts who were already submitting before applications existed stay approved
    conn.execute("INSERT OR IGNORE INTO scouts(discord_id, status) SELECT DISTINCT scout_id, 'approved' FROM submissions")
    # prospects that were already moving before approve/deny existed count as approved
    conn.execute("UPDATE submissions SET decision='approved' WHERE decision='review' AND status IN ('effective','terminated')")
    conn.commit()
    return conn


# ---------- helpers ----------
def norm_handle(h) -> str:
    return str(h or "").strip().lstrip("@").strip().lower()


def norm_month(m) -> str:
    s = re.sub(r"\D", "", str(m or ""))
    if len(s) != 6 or not (1 <= int(s[4:]) <= 12):
        raise ValueError(f"Month must look like 202608 or 2026-08 (got {m!r})")
    return s


def to_cents(value) -> int:
    d = Decimal(str(value).replace("$", "").replace(",", "").strip() or "0")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def split_lines(gross_cents: int, scout_id, manager_id=None):
    """This is the scouting server: only the scout's 5% is tracked. Returns [(role, payee_id, cents)]."""
    if not scout_id:
        return []
    cents = int((Decimal(gross_cents) * SCOUT_PCT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return [("scout", str(scout_id), cents)]


# ---------- creators ----------
def upsert_creator(conn, handle, scout_id=None, manager_id=None):
    h = norm_handle(handle)
    if not h:
        raise ValueError("Handle is empty")
    conn.execute("INSERT OR IGNORE INTO creators(handle) VALUES (?)", (h,))
    if scout_id is not None:
        conn.execute("UPDATE creators SET scout_id=? WHERE handle=?", (str(scout_id), h))
    if manager_id is not None:
        conn.execute("UPDATE creators SET manager_id=? WHERE handle=?", (str(manager_id), h))
    conn.commit()
    return conn.execute("SELECT * FROM creators WHERE handle=?", (h,)).fetchone()


# ---------- parsing the TikTok export ----------
REQUIRED = ["Data Month", "Handle", "Estimated bonus"]


def parse_export(data: bytes, filename: str):
    """Read the TikTok LIVE Creator Network export (csv or xlsx) -> [{month, handle, gross_cents}]."""
    name = filename.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data), dtype=str)
    elif name.endswith((".xlsx", ".xlsm")):
        df = pd.read_excel(io.BytesIO(data), dtype=str)
    else:
        raise ValueError("Please upload a .csv or .xlsx file (export from Numbers first if needed).")
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Missing column(s): {', '.join(missing)}")

    def find(prefix):
        return next((c for c in df.columns if c.lower().startswith(prefix)), None)
    c_dia, c_days, c_hrs = find("diamonds"), find("valid days"), find("live duration")

    def num(r, col):
        try:
            return float(str(r[col]).replace(",", "")) if col and not pd.isna(r[col]) else None
        except ValueError:
            return None

    totals, stats = {}, {}
    for _, r in df.iterrows():
        handle = norm_handle(r["Handle"])
        if not handle or pd.isna(r["Data Month"]):
            continue  # blank trailing rows
        month = norm_month(r["Data Month"])
        cents = to_cents(r["Estimated bonus"] if not pd.isna(r["Estimated bonus"]) else 0)
        totals[(month, handle)] = totals.get((month, handle), 0) + cents
        d = num(r, c_dia)
        stats[(month, handle)] = {"diamonds": int(d) if d is not None else None,
                                  "valid_days": num(r, c_days), "hours": num(r, c_hrs)}
    return [{"month": m, "handle": h, "gross_cents": c, **stats[(m, h)]} for (m, h), c in totals.items()]


# ---------- importing ----------
def import_rows(conn, rows):
    report = {"imported": 0, "already": 0, "zero": 0, "no_scout": [], "unmatched": [], "terminated": [], "forfeited": [], "gross": 0,
              "scout": 0, "months": set()}
    for row in rows:
        month, handle, gross = row["month"], row["handle"], row["gross_cents"]
        report["months"].add(month)
        c = conn.execute("SELECT * FROM creators WHERE handle=?", (handle,)).fetchone()
        if c is None:
            report["unmatched"].append(handle)
            continue
        conn.execute("INSERT OR REPLACE INTO creator_stats(month, handle, diamonds, valid_days, hours) VALUES (?,?,?,?,?)",
                     (month, handle, row.get("diamonds"), row.get("valid_days"), row.get("hours")))
        if creator_status(conn, handle) == "terminated":
            report["terminated"].append(handle)  # no revenue once terminated, so no 5%
            continue
        if scout_forfeited(conn, c["scout_id"]):
            report["forfeited"].append(handle)  # scout was removed for breaking the agreement: no commission for later months
            continue
        if gross == 0:
            report["zero"] += 1
            continue
        if conn.execute("SELECT 1 FROM ledger WHERE month=? AND handle=? AND kind='import'",
                        (month, handle)).fetchone():
            report["already"] += 1
            continue
        lines = split_lines(gross, c["scout_id"])
        if not lines:
            report["no_scout"].append(handle)  # not credited; re-import after a scout is linked
            continue
        for role, payee, cents in lines:
            conn.execute(
                "INSERT INTO ledger(month, handle, role, payee_id, amount_cents, gross_cents, kind) "
                "VALUES (?,?,?,?,?,?,'import')", (month, handle, role, payee, cents, gross))
            report["scout"] += cents
        report["imported"] += 1
        report["gross"] += gross
    conn.commit()
    return report


def add_adjustment(conn, handle, month, gross_change_cents, note=""):
    """Correct a month after TikTok finalizes it. Uses the creator's current scout/manager."""
    h, m = norm_handle(handle), norm_month(month)
    c = conn.execute("SELECT * FROM creators WHERE handle=?", (h,)).fetchone()
    if c is None:
        raise ValueError(f"@{h} isn't registered. Use /addcreator first.")
    lines = split_lines(gross_change_cents, c["scout_id"])
    if not lines:
        raise ValueError(f"@{h} has no scout, so there is nothing to adjust.")
    for role, payee, cents in lines:
        conn.execute(
            "INSERT INTO ledger(month, handle, role, payee_id, amount_cents, gross_cents, kind, note) "
            "VALUES (?,?,?,?,?,?,'adjustment',?)", (m, h, role, payee, cents, gross_change_cents, note))
    conn.commit()
    return lines


# ---------- reporting ----------
def earnings_for(conn, payee_id):
    return conn.execute(
        "SELECT month, handle, role, SUM(amount_cents) AS cents, MIN(paid) AS all_paid "
        "FROM ledger WHERE payee_id=? GROUP BY month, handle, role ORDER BY month DESC, handle",
        (str(payee_id),)).fetchall()


def payouts_due(conn, month=None):
    q = ("SELECT payee_id, SUM(amount_cents) AS cents FROM ledger "
         "WHERE paid=0 AND payee_id != ? ")
    args = [OWNER]
    if month:
        q += "AND month=? "
        args.append(norm_month(month))
    q += "GROUP BY payee_id HAVING cents != 0 ORDER BY cents DESC"
    return conn.execute(q, args).fetchall()


def mark_paid(conn, payee_id, month=None):
    q = "UPDATE ledger SET paid=1, paid_at=CURRENT_TIMESTAMP WHERE paid=0 AND payee_id=?"
    args = [str(payee_id)]
    if month:
        q += " AND month=?"
        args.append(norm_month(month))
    cur = conn.execute(q, args)
    conn.commit()
    return cur.rowcount


# ---------- payees + W-9 tracking ----------
# The bot only records THAT a W-9 is on file. Never collect the form itself in Discord (it has an SSN/EIN).
def manager_key(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    if not slug:
        raise ValueError("Manager name is empty")
    return "m:" + slug


def ensure_payee(conn, payee_id, name=None):
    conn.execute("INSERT OR IGNORE INTO payees(payee_id) VALUES (?)", (str(payee_id),))
    if name:
        conn.execute("UPDATE payees SET name=? WHERE payee_id=?", (name, str(payee_id)))
    conn.commit()


def set_w9(conn, payee_id, on_file=True, envelope_id=None):
    ensure_payee(conn, payee_id)
    conn.execute("UPDATE payees SET w9_on_file=?, w9_date=CASE WHEN ?=1 THEN CURRENT_TIMESTAMP END, "
                 "w9_envelope_id=? WHERE payee_id=?",
                 (1 if on_file else 0, 1 if on_file else 0, envelope_id if on_file else None, str(payee_id)))
    conn.commit()


def has_w9(conn, payee_id) -> bool:
    r = conn.execute("SELECT w9_on_file FROM payees WHERE payee_id=?", (str(payee_id),)).fetchone()
    return bool(r and r["w9_on_file"])


def payee_name(conn, payee_id):
    r = conn.execute("SELECT name FROM payees WHERE payee_id=?", (str(payee_id),)).fetchone()
    return (r["name"] if r and r["name"] else None)


# ---------- scouting submissions ----------
STATUS_LABELS = {"pending": "Pending", "effective": "Effective", "terminated": "Terminated"}
_IG_USER = re.compile(r"^[a-z0-9._]{1,30}$")
_IG_RESERVED = {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "direct"}


class DuplicateSubmission(Exception):
    def __init__(self, row):
        self.row = row


def parse_instagram(username: str, link: str):
    """Clean the username and make sure the link really points at that profile."""
    from urllib.parse import urlparse
    u = norm_handle(username)
    if not _IG_USER.match(u):
        raise ValueError("That doesn't look like an Instagram username (letters, numbers, . and _ only).")
    raw = link.strip()
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    host = (p.netloc or "").lower().removeprefix("www.")
    if host not in ("instagram.com", "m.instagram.com"):
        raise ValueError("The link must be an instagram.com profile link.")
    seg = [x for x in p.path.split("/") if x]
    if not seg or seg[0].lower() in _IG_RESERVED:
        raise ValueError("Please paste the profile link (instagram.com/username), not a post.")
    if seg[0].lower() != u:
        raise ValueError(f"The link is for @{seg[0].lower()} but the username says @{u}. Please check both.")
    return u, f"https://instagram.com/{u}"


def add_submission(conn, username, url, scout_id):
    try:
        cur = conn.execute("INSERT INTO submissions(ig_username, ig_url, scout_id, status) VALUES (?,?,?,'pending')",
                           (username, url, str(scout_id)))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise DuplicateSubmission(conn.execute("SELECT * FROM submissions WHERE ig_username=?",
                                               (username,)).fetchone())


def get_submission(conn, sub_id):
    return conn.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()


def set_submission_status(conn, sub_id, status):
    if status not in STATUS_LABELS:
        raise ValueError("Unknown status")
    conn.execute("UPDATE submissions SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, sub_id))
    conn.commit()


def submissions_for(conn, scout_id):
    return conn.execute("SELECT * FROM submissions WHERE scout_id=? ORDER BY id DESC", (str(scout_id),)).fetchall()


# ---------- backup ----------
def backup_to(conn, dest_path: str):
    """Write a consistent snapshot of the live database to dest_path (safe while the bot is running)."""
    dest = sqlite3.connect(dest_path)
    try:
        conn.backup(dest)
    finally:
        dest.close()



# ---------- management: signing, roster, benchmarks ----------
def set_review_msg(conn, sub_id, msg_id):
    conn.execute("UPDATE submissions SET review_msg_id=? WHERE id=?", (str(msg_id), sub_id))
    conn.commit()


def roster(conn):
    """Everyone and who they're attached to: {'scouts': [...], 'managers': [...], 'creators': [...]}."""
    creators = conn.execute("SELECT * FROM creators ORDER BY handle").fetchall()
    scouts, managers = {}, {}
    for c in creators:
        if c["scout_id"]:
            scouts.setdefault(c["scout_id"], []).append(c["handle"])
        if c["manager_id"]:
            managers.setdefault(c["manager_id"], []).append(c["handle"])
    for s_ in conn.execute("SELECT scout_id, COUNT(*) n FROM submissions GROUP BY scout_id"):
        scouts.setdefault(s_["scout_id"], [])
    return {"scouts": scouts, "managers": managers, "creators": creators}


def submission_counts(conn):
    return {r["scout_id"]: r["n"] for r in conn.execute("SELECT scout_id, COUNT(*) n FROM submissions GROUP BY scout_id")}


def get_setting(conn, key, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()


def below_benchmark(conn, month):
    """Registered creators under the benchmark for a month (or missing from that month's export)."""
    m = norm_month(month)
    min_days = float(get_setting(conn, "min_valid_days", 0))
    min_hours = float(get_setting(conn, "min_hours", 0))
    out = []
    for c in conn.execute("SELECT * FROM creators ORDER BY handle"):
        st = conn.execute("SELECT * FROM creator_stats WHERE month=? AND handle=?", (m, c["handle"])).fetchone()
        days = (st["valid_days"] or 0) if st else 0
        hours = (st["hours"] or 0) if st else 0
        reasons = []
        if st is None:
            reasons.append("not in this month's export")
        else:
            if days < min_days:
                reasons.append(f"{days:g}/{min_days:g} valid days")
            if hours < min_hours:
                reasons.append(f"{hours:g}/{min_hours:g} hours")
        if reasons:
            out.append((c, "; ".join(reasons)))
    return out


def get_submission_by_ig(conn, ig_username):
    return conn.execute("SELECT * FROM submissions WHERE ig_username=?", (norm_handle(ig_username),)).fetchone()


# ---------- status automation from the TikTok "Manage creators" export ----------


def _find_header(df):
    for i in range(min(len(df), 10)):
        vals = [str(v).strip().lower() for v in df.iloc[i].tolist()]
        if "creator's username" in vals:
            return i
    return None


def parse_creators_export(data: bytes, filename: str):
    """Read TikTok's 'Manage creators' export (header on row 2) -> [{handle, relationship, valid_days, last_live,...}]."""
    name = filename.lower()
    if name.endswith(".csv"):
        raw = pd.read_csv(io.BytesIO(data), dtype=str, header=None, keep_default_na=False)
    elif name.endswith((".xlsx", ".xlsm")):
        raw = pd.read_excel(io.BytesIO(data), dtype=str, header=None, keep_default_na=False)
    else:
        raise ValueError("Please upload a .csv or .xlsx file.")
    h = _find_header(raw)
    if h is None:
        raise ValueError("This doesn't look like the 'Manage creators' export (no \"Creator's username\" column).")
    cols = [str(c).strip() for c in raw.iloc[h].tolist()]
    body = raw.iloc[h + 1:].copy()
    body.columns = cols

    def col(prefix):
        return next((c for c in cols if c.lower().startswith(prefix)), None)

    c_user, c_rel = col("creator's username"), col("relationship status")
    c_days, c_last, c_notes = col("valid go live days"), col("last live"), col("notes")
    c_mgr = col("creator network manager")

    def num(v):
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            return 0.0

    out = []
    for _, r in body.iterrows():
        handle = norm_handle(r[c_user])
        if not handle:
            continue
        last = str(r[c_last]).strip() if c_last else ""
        out.append({"handle": handle,
                    "relationship": str(r[c_rel]).strip() if c_rel else "",
                    "valid_days": num(r[c_days]) if c_days else 0.0,
                    "last_live": "" if last in ("", "-") else last,
                    "notes": str(r[c_notes]).strip() if c_notes else "",
                    "manager": str(r[c_mgr]).strip().lower() if c_mgr else ""})
    return out


def ig_from_notes(text: str):
    """Find an Instagram username in free text, e.g. 'IG: @janedoe', 'instagram janedoe', 'instagram.com/janedoe'."""
    t = str(text or "")
    m = (re.search(r"instagram\.com/([A-Za-z0-9._]{1,30})", t, re.I)
         or re.search(r"\b(?:instagram|insta|ig)\b\s*[:\-]?\s*@?([A-Za-z0-9._]{1,30})", t, re.I))
    if not m:
        return None
    u = m.group(1).lower().rstrip(".")
    return u if _IG_USER.match(u) and u not in _IG_RESERVED else None


def derive_status(row) -> str:
    """Mirror TikTok's Relationship status: Effective -> effective, Terminated -> terminated, anything else -> pending."""
    rel = row["relationship"].strip().lower()
    if rel == "effective":
        return "effective"
    if "terminat" in rel:
        return "terminated"
    return "pending"


def clean_email(value):
    v = str(value or "").strip().lower()
    if not v:
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
        raise ValueError("That email doesn't look right.")
    return v


def clean_phone(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not 7 <= len(digits) <= 15:
        raise ValueError("That phone number doesn't look right (7 to 15 digits).")
    return ("+" if raw.lstrip().startswith("+") else "") + digits


def link_handles(conn, tiktok_handle, ig_username, email=None, phone=None):
    """Connect a TikTok username to the Instagram prospect it came from, and save their contact details."""
    email, phone = clean_email(email), clean_phone(phone)
    sub = get_submission_by_ig(conn, ig_username)
    if sub is None:
        raise ValueError(f"No submission found for @{norm_handle(ig_username)}.")
    c = upsert_creator(conn, tiktok_handle, scout_id=sub["scout_id"])
    conn.execute("UPDATE creators SET ig_username=? WHERE handle=?", (sub["ig_username"], c["handle"]))
    if email:
        conn.execute("UPDATE creators SET email=? WHERE handle=?", (email, c["handle"]))
    if phone:
        conn.execute("UPDATE creators SET phone=? WHERE handle=?", (phone, c["handle"]))
    conn.commit()
    return sub


def sync_creators(conn, rows):
    """Set each submission's status to match TikTok's relationship status (it can change either way).
    Returns {'changed': [(submission_id, ig_username, scout_id, old, new)], 'registered': [...], 'unmatched': [...]}."""
    report = {"changed": [], "registered": [], "unmatched": [], "same": 0, "managed_changed": []}
    for row in rows:
        handle = row["handle"]
        new_ts = derive_status(row)
        old_ts = conn.execute("SELECT tiktok_status FROM managed_creators WHERE handle=?", (handle,)).fetchone()
        old_ts = old_ts["tiktok_status"] if old_ts else None
        conn.execute("INSERT INTO managed_creators(handle, tiktok_status, manager_email, last_live, seen_at) "
                     "VALUES (?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(handle) DO UPDATE SET tiktok_status=excluded.tiktok_status, "
                     "manager_email=excluded.manager_email, last_live=excluded.last_live, seen_at=CURRENT_TIMESTAMP",
                     (handle, new_ts, row.get("manager") or None, row.get("last_live") or None))
        if old_ts != new_ts:
            report["managed_changed"].append((handle, old_ts, new_ts))
        linked = conn.execute("SELECT ig_username FROM creators WHERE handle=?", (handle,)).fetchone()
        sub = get_submission_by_ig(conn, linked["ig_username"]) if linked and linked["ig_username"] else None
        if sub is None:                       # same name on both platforms
            sub = get_submission_by_ig(conn, handle)
        if sub is None:                       # manager wrote the Instagram name in TikTok's Notes while onboarding
            ig = ig_from_notes(row.get("notes", ""))
            sub = get_submission_by_ig(conn, ig) if ig else None
        if sub is None:
            report["unmatched"].append(handle)
            continue
        new, old = derive_status(row), sub["status"]
        if new != "pending" and new != old:
            set_submission_status(conn, sub["id"], new)
            report["changed"].append((sub["id"], sub["ig_username"], sub["scout_id"], old, new))
        else:
            report["same"] += 1
        if new == "effective":
            had = conn.execute("SELECT scout_id FROM creators WHERE handle=?", (handle,)).fetchone()
            if not had or not had["scout_id"]:
                upsert_creator(conn, handle, scout_id=sub["scout_id"])
                conn.execute("UPDATE creators SET ig_username=? WHERE handle=?", (sub["ig_username"], handle))
                conn.commit()
                report["registered"].append(handle)
    conn.commit()
    return report


# ---------- W-9 through an e-sign portal (DocuSign) ----------
def verify_hmac(body: bytes, headers: dict, keys) -> bool:
    """DocuSign Connect sends Base64(HMAC-SHA256(body, key)) in X-DocuSign-Signature-1, -2, ... for rotated keys."""
    import base64, hashlib, hmac
    sigs = [v for k, v in headers.items() if k.lower().startswith("x-docusign-signature-")]
    for key in keys:
        want = base64.b64encode(hmac.new(key.encode(), body, hashlib.sha256).digest()).decode()
        if any(hmac.compare_digest(want, sig) for sig in sigs):
            return True
    return False


def find_scout_id(payload):
    """Look anywhere in the payload for a field/tab named ScoutID and return its numeric value."""
    def norm(x):
        return re.sub(r"[^a-z0-9]", "", str(x).lower())
    if isinstance(payload, dict):
        label = payload.get("tabLabel") or payload.get("name") or payload.get("label")
        if label and norm(label) == "scoutid" and re.fullmatch(r"\d{1,25}", str(payload.get("value", "")).strip()):
            return str(payload["value"]).strip()
        for k, v in payload.items():
            if norm(k) == "scoutid" and re.fullmatch(r"\d{1,25}", str(v).strip()):
                return str(v).strip()
            found = find_scout_id(v)
            if found:
                return found
    elif isinstance(payload, list):
        for v in payload:
            found = find_scout_id(v)
            if found:
                return found
    return None


def record_w9_event(conn, envelope_id, signer_name=None, signer_email=None, scout_id=None):
    """Store the completed form's envelope id. If we know the scout, mark their W-9 on file. Returns True if linked."""
    conn.execute("INSERT OR IGNORE INTO w9_events(envelope_id, signer_name, signer_email, scout_id) VALUES (?,?,?,?)",
                 (envelope_id, signer_name, signer_email, str(scout_id) if scout_id else None))
    conn.commit()
    if scout_id:
        set_w9(conn, scout_id, True, envelope_id)
        return True
    return False


def assign_w9_event(conn, envelope_id, scout_id):
    if conn.execute("SELECT 1 FROM w9_events WHERE envelope_id=?", (envelope_id,)).fetchone() is None:
        raise ValueError("No W-9 with that envelope id.")
    conn.execute("UPDATE w9_events SET scout_id=? WHERE envelope_id=?", (str(scout_id), envelope_id))
    conn.commit()
    set_w9(conn, scout_id, True, envelope_id)


def creator_status(conn, handle):
    """Status of the prospect this creator came from (pending / effective / terminated), or None if unknown."""
    c = conn.execute("SELECT ig_username FROM creators WHERE handle=?", (norm_handle(handle),)).fetchone()
    ig = c["ig_username"] if c and c["ig_username"] else handle
    sub = get_submission_by_ig(conn, ig)
    return sub["status"] if sub else None


# ---------- scout applications ----------
class AlreadyApplied(Exception):
    def __init__(self, status):
        self.status = status


def apply_scout(conn, discord_id, full_name, email, phone, agreed=False, agreement_version=None, agreement_text=None):
    name = " ".join(str(full_name or "").split())
    if len(name.split()) < 2:
        raise ValueError("Please enter your full name (first and last).")
    if not agreed:
        raise ValueError("You need to check the box to agree to the Scout Agreement.")
    email, phone = clean_email(email), clean_phone(phone)
    if not email or not phone:
        raise ValueError("Please enter both your email and phone number.")
    row = conn.execute("SELECT status FROM scouts WHERE discord_id=?", (str(discord_id),)).fetchone()
    if row and row["status"] in ("pending", "approved"):
        raise AlreadyApplied(row["status"])
    conn.execute("INSERT INTO scouts(discord_id, full_name, email, phone, status, agreement_version, agreed_at, agreement_text) "
                 "VALUES (?,?,?,?,'pending',?,CURRENT_TIMESTAMP,?) "
                 "ON CONFLICT(discord_id) DO UPDATE SET full_name=excluded.full_name, email=excluded.email, "
                 "phone=excluded.phone, status='pending', agreement_version=excluded.agreement_version, "
                 "agreed_at=CURRENT_TIMESTAMP, agreement_text=excluded.agreement_text, "
                 "updated_at=CURRENT_TIMESTAMP",
                 (str(discord_id), name, email, phone, agreement_version, agreement_text))
    conn.commit()
    return get_scout(conn, discord_id)


def get_scout(conn, discord_id):
    return conn.execute("SELECT * FROM scouts WHERE discord_id=?", (str(discord_id),)).fetchone()


def set_scout_status(conn, discord_id, status):
    if status not in ("approved", "denied"):
        raise ValueError("status must be approved or denied")
    conn.execute("UPDATE scouts SET status=?, updated_at=CURRENT_TIMESTAMP WHERE discord_id=?", (status, str(discord_id)))
    s = get_scout(conn, discord_id)
    if status == "approved" and s:
        ensure_payee(conn, discord_id, s["full_name"])
    conn.commit()
    return s


def export_agreements(conn):
    """Zip (bytes) with one text file per scout who accepted the agreement: who, when, which version, and the full text."""
    import io, zipfile
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for s in conn.execute("SELECT * FROM scouts WHERE agreed_at IS NOT NULL ORDER BY full_name"):
            safe = re.sub(r"[^A-Za-z0-9]+", "_", s["full_name"] or s["discord_id"]).strip("_") or s["discord_id"]
            header = (f"ACCEPTANCE RECORD\nName: {s['full_name']}\nEmail: {s['email']}\nPhone: {s['phone']}\n"
                      f"Discord ID: {s['discord_id']}\nAccepted (UTC): {s['agreed_at']}\nAgreement version: {s['agreement_version']}\n"
                      f"Accepted by checking the box and submitting the application.\n\n{'-' * 60}\n\n")
            body = s["agreement_text"] or "(Full text was not saved for this acceptance. See the version number above.)"
            z.writestr(f"{safe}_{s['discord_id']}.txt", header + body)
            n += 1
    return buf.getvalue(), n


def export_w9_list(conn):
    """CSV text (Name, Email) of approved scouts whose W-9 isn't on file, ready for DocuSign Bulk Send."""
    import csv, io
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Name", "Email"])
    n = 0
    for s in conn.execute("SELECT * FROM scouts WHERE status='approved' AND full_name IS NOT NULL AND email IS NOT NULL "
                          "ORDER BY full_name"):
        if not has_w9(conn, s["discord_id"]):
            w.writerow([s["full_name"], s["email"]])
            n += 1
    return out.getvalue(), n


def scout_forfeited(conn, discord_id):
    s = get_scout(conn, discord_id) if discord_id else None
    return bool(s and s["forfeited"])


def remove_scout(conn, discord_id, for_breach=False):
    """Remove a scout. They can't submit new prospects. Existing creators keep earning them 5% unless it was for breach."""
    s = get_scout(conn, discord_id)
    if s is None:
        raise ValueError("That person isn't a scout in the system.")
    conn.execute("UPDATE scouts SET status='removed', forfeited=?, updated_at=CURRENT_TIMESTAMP WHERE discord_id=?",
                 (1 if for_breach else 0, str(discord_id)))
    conn.commit()
    return get_scout(conn, discord_id)


def export_payouts(conn, month=None):
    """CSV text (Name, Amount, Month) of what's owed to scouts whose paperwork is on file, plus who is held back."""
    import csv, io
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Name", "Amount", "Month"])
    n, held = 0, []
    for d in payouts_due(conn, month):
        pid = d["payee_id"]
        s = get_scout(conn, pid)
        name = (s["full_name"] if s and s["full_name"] else None) or payee_name(conn, pid) or pid
        if d["cents"] <= 0:
            continue
        if not has_w9(conn, pid):
            held.append(name)
            continue
        w.writerow([name, f"{d['cents'] / 100:.2f}", norm_month(month) if month else "all unpaid"])
        n += 1
    return out.getvalue(), n, held


def scout_approved(conn, discord_id):
    s = get_scout(conn, discord_id)
    return bool(s and s["status"] == "approved")


DECISIONS = {"review": "Needs review", "approved": "Approved", "denied": "Denied"}


def set_decision(conn, sub_id, decision):
    if decision not in ("approved", "denied"):
        raise ValueError("decision must be approved or denied")
    conn.execute("UPDATE submissions SET decision=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (decision, sub_id))
    conn.commit()
    return get_submission(conn, sub_id)


def export_approved(conn, only_new=True):
    """CSV text of approved prospects (for your DM tool). Marks them exported so the next file only has new ones."""
    import csv, io
    q = "SELECT * FROM submissions WHERE decision='approved'" + (" AND exported_at IS NULL" if only_new else "") + " ORDER BY id"
    rows = conn.execute(q).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["instagram_username"])
    for r in rows:
        w.writerow([r["ig_username"]])
    conn.execute(f"UPDATE submissions SET exported_at=CURRENT_TIMESTAMP WHERE decision='approved' AND exported_at IS NULL"
                 if only_new else "SELECT 1")
    conn.commit()
    return out.getvalue(), len(rows)


def creators_by_ig(conn, ig_username):
    return conn.execute("SELECT * FROM creators WHERE ig_username=?", (norm_handle(ig_username),)).fetchone()


def pending_submissions(conn):
    return conn.execute("SELECT * FROM submissions WHERE status='pending' ORDER BY id").fetchall()



# ---------- creator onboarding (the creator enters their own details in the creator server) ----------
class NotInvited(Exception):
    pass


class AlreadyClaimed(Exception):
    pass


def assign_manager(conn, handle, manager_email, manager_name=None):
    key = manager_key(manager_email)
    ensure_payee(conn, key, (manager_name or manager_email).strip())
    conn.execute("UPDATE creators SET manager_id=? WHERE handle=?", (key, norm_handle(handle)))
    conn.commit()
    return key


def latest_booking(conn, email):
    return conn.execute("SELECT * FROM bookings WHERE invitee_email=? AND status='active' "
                        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (str(email or "").strip().lower(),)).fetchone()


def reconcile_by_email(conn, email):
    """If a creator and an active booking share an email, the booking's host becomes that creator's manager.
    Works whichever happens first: the booking or the onboarding. Returns [(handle, booking)]."""
    email = str(email or "").strip().lower()
    booking = latest_booking(conn, email)
    if not email or booking is None or not booking["manager_email"]:
        return []
    out = []
    for cr in conn.execute("SELECT handle FROM creators WHERE lower(email)=?", (email,)).fetchall():
        assign_manager(conn, cr["handle"], booking["manager_email"], booking["manager_name"])
        out.append((cr["handle"], booking))
    return out


def record_booking(conn, uid, invitee_email, invitee_name, manager_email, manager_name, start_time):
    email = str(invitee_email or "").strip().lower()
    if not uid or not email:
        raise ValueError("Booking is missing an id or invitee email.")
    mgr = str(manager_email or "").strip().lower() or None
    conn.execute("INSERT INTO bookings(uid, invitee_email, invitee_name, manager_email, manager_name, start_time, status) "
                 "VALUES (?,?,?,?,?,?, 'active') ON CONFLICT(uid) DO UPDATE SET invitee_email=excluded.invitee_email, "
                 "invitee_name=excluded.invitee_name, manager_email=excluded.manager_email, "
                 "manager_name=excluded.manager_name, start_time=excluded.start_time, status='active'",
                 (uid, email, invitee_name, mgr, manager_name, start_time))
    # one intro call per creator: an older active booking for the same email is replaced (reschedules)
    conn.execute("UPDATE bookings SET status='superseded' WHERE invitee_email=? AND uid!=? AND status='active'", (email, uid))
    conn.commit()
    return reconcile_by_email(conn, email)


def cancel_booking(conn, uid):
    cur = conn.execute("UPDATE bookings SET status='canceled' WHERE uid=? AND status='active'", (uid,))
    conn.commit()
    return conn.execute("SELECT * FROM bookings WHERE uid=?", (uid,)).fetchone() if cur.rowcount else None


def onboard_creator(conn, ig_username, tiktok, email, phone, discord_id=None):
    """Connect scout -> creator -> TikTok -> contact -> manager from the creator's own entries."""
    email, phone = clean_email(email), clean_phone(phone)
    if not email or not phone:
        raise ValueError("Please enter both your email and phone number.")
    handle = norm_handle(tiktok)
    if not handle:
        raise ValueError("Please enter your TikTok username.")
    sub = get_submission_by_ig(conn, ig_username)
    if sub is None or sub["decision"] != "approved":
        raise NotInvited()
    other = conn.execute("SELECT handle FROM creators WHERE ig_username=? AND handle!=?", (sub["ig_username"], handle)).fetchone()
    mine = conn.execute("SELECT ig_username FROM creators WHERE handle=?", (handle,)).fetchone()
    if other or (mine and mine["ig_username"] and mine["ig_username"] != sub["ig_username"]):
        raise AlreadyClaimed()
    link_handles(conn, handle, sub["ig_username"], email, phone)
    if discord_id:
        conn.execute("UPDATE creators SET discord_id=? WHERE handle=?", (str(discord_id), handle))
        conn.commit()
    reconcile_by_email(conn, email)
    return {"sub": sub, "handle": handle, "booking": latest_booking(conn, email)}


# ---------- scheduling tool webhooks (Calendly / Cal.com) ----------
def verify_calendly(body: bytes, header: str, key: str, now=None, tolerance=3600) -> bool:
    """Calendly-Webhook-Signature: t=<unix>,v1=<hex>; v1 = HMAC-SHA256(key, '<t>.<raw body>')."""
    import hashlib, hmac, time
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t, v1 = parts["t"], parts["v1"]
        if abs((now or time.time()) - int(t)) > tolerance:
            return False
    except (KeyError, ValueError, AttributeError):
        return False
    want = hmac.new(key.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, v1)


def verify_calcom(body: bytes, header: str, secret: str) -> bool:
    """x-cal-signature-256 = hex HMAC-SHA256(secret, raw body)."""
    import hashlib, hmac
    if not header:
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header.strip().lower())


def parse_calendly(payload):
    """-> ('created'|'canceled', dict) or None. Based on Calendly's invitee webhook (invitee.created / invitee.canceled)."""
    kind = {"invitee.created": "created", "invitee.canceled": "canceled"}.get(payload.get("event"))
    if not kind:
        return None
    p = payload.get("payload") or {}
    se = p.get("scheduled_event") or {}
    host = (se.get("event_memberships") or [{}])[0]
    return kind, {"uid": p.get("uri"), "invitee_email": p.get("email"), "invitee_name": p.get("name"),
                  "manager_email": host.get("user_email"), "manager_name": host.get("user_name"),
                  "start_time": se.get("start_time")}


def parse_calcom(payload):
    """-> ('created'|'canceled', dict) or None. Based on Cal.com's BOOKING_* webhooks."""
    kind = {"BOOKING_CREATED": "created", "BOOKING_RESCHEDULED": "created",
            "BOOKING_CANCELLED": "canceled"}.get(payload.get("triggerEvent"))
    if not kind:
        return None
    p = payload.get("payload") or {}
    att = (p.get("attendees") or [{}])[0]
    org = p.get("organizer") or {}
    return kind, {"uid": p.get("uid"), "invitee_email": att.get("email"), "invitee_name": att.get("name"),
                  "manager_email": org.get("email"), "manager_name": org.get("name"), "start_time": p.get("startTime")}
