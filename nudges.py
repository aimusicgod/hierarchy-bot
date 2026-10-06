"""Performance check-ins ("nudges"): read a TikTok LIVE Backstage CSV, apply the rules in nudge_rules.json,
and produce supportive messages for staff to approve. No Discord code, and the bot never touches Backstage logins."""
import datetime
import io
import json
import os
import random
import re

import pandas as pd

import core
import creators

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nudge_rules.json")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    for r in cfg.get("rules", []):
        if not r.get("id") or not r.get("messages"):
            raise ValueError("Every nudge rule needs an id and at least one message.")
        if r.get("type") not in ("days_since_live", "weekly_pct_below"):
            raise ValueError(f"Unknown nudge rule type: {r.get('type')}")
    return cfg


def weekday_index(name):
    n = str(name).strip().lower()
    if n not in WEEKDAYS:
        raise ValueError(f"'{name}' isn't a weekday.")
    return WEEKDAYS.index(n)


def iso_week(today):
    y, w, _ = today.isocalendar()
    return f"{y}-W{w:02d}"


# ---------- reading the CSV ----------
def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _date(v):
    m = re.search(r"(\d{4})[:/\-](\d{2})[:/\-](\d{2})", str(v or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def parse_stats(data: bytes, filename: str, config):
    name = filename.lower()
    if name.endswith(".csv"):
        raw = pd.read_csv(io.BytesIO(data), dtype=str, header=None, keep_default_na=False)
    elif name.endswith((".xlsx", ".xlsm")):
        raw = pd.read_excel(io.BytesIO(data), dtype=str, header=None, keep_default_na=False)
    else:
        raise ValueError("Please upload a .csv or .xlsx file.")
    aliases = config["columns"]
    hdr = None
    for i in range(min(len(raw), 12)):
        vals = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
        if any(v in aliases["handle"] for v in vals):
            hdr = i
            break
    if hdr is None:
        raise ValueError("I couldn't find a creator username column. The file needs a column named "
                         "\"Creator's username\" or \"Handle\". You can add other names in nudge_rules.json.")
    cols = [str(c).strip() for c in raw.iloc[hdr].tolist()]
    body = raw.iloc[hdr + 1:].copy()
    body.columns = cols
    low = {c.lower(): c for c in cols}

    def pick(key):
        for a in aliases[key]:
            if a in low:
                return low[a]
        for c in cols:                      # allow "Valid go LIVE days in L30D (d)"-style suffixes
            if any(c.lower().startswith(a) for a in aliases[key]):
                return c
        return None

    c = {k: pick(k) for k in aliases}
    rows = []
    for _, r in body.iterrows():
        h = core.norm_handle(r[c["handle"]])
        if not h:
            continue
        rows.append({"handle": h,
                     "last_live": _date(r[c["last_live"]]) if c["last_live"] else None,
                     "week_hours": _num(r[c["week_hours"]]) if c["week_hours"] else None,
                     "week_days": _num(r[c["week_days"]]) if c["week_days"] else None,
                     "hours_30d": _num(r[c["hours_30d"]]) if c["hours_30d"] else None,
                     "days_30d": _num(r[c["days_30d"]]) if c["days_30d"] else None})
    found = [k for k in aliases if c[k]]
    return rows, found


def save_stats(conn, rows, now=None):
    now = (now or creators.utcnow()).isoformat()
    conn.execute("DELETE FROM live_stats")
    for r in rows:
        conn.execute("INSERT INTO live_stats(handle, last_live, week_hours, week_days, hours_30d, days_30d, uploaded_at) "
                     "VALUES (?,?,?,?,?,?,?)", (r["handle"], r["last_live"], r["week_hours"], r["week_days"],
                                                r["hours_30d"], r["days_30d"], now))
    core.set_setting(conn, "stats:uploaded_at", now)
    conn.commit()
    return len(rows)


def stats_age_days(conn, now=None):
    ts = core.get_setting(conn, "stats:uploaded_at")
    if not ts:
        return None
    return ((now or creators.utcnow()) - creators.parse_iso(ts)).total_seconds() / 86400


# ---------- skip list and goals ----------
def skip(conn, handle, until=None, note=""):
    conn.execute("INSERT OR REPLACE INTO nudge_skip(handle, until, note) VALUES (?,?,?)",
                 (core.norm_handle(handle), until, note))
    conn.commit()


def unskip(conn, handle):
    conn.execute("DELETE FROM nudge_skip WHERE handle=?", (core.norm_handle(handle),))
    conn.commit()


def is_skipped(conn, handle, today):
    r = conn.execute("SELECT until FROM nudge_skip WHERE handle=?", (core.norm_handle(handle),)).fetchone()
    if not r:
        return False
    return r["until"] is None or str(r["until"]) >= today.isoformat()


def set_goal(conn, handle, hours):
    conn.execute("UPDATE creators SET weekly_goal_hours=? WHERE handle=?", (hours, core.norm_handle(handle)))
    conn.commit()


def goal_for(creator, config):
    g = creator["weekly_goal_hours"] if creator is not None else None
    return float(g) if g else float(config.get("default_weekly_goal_hours", 10))


# ---------- the rules ----------
def days_since(last_live, today):
    if not last_live:
        return None
    return (today - datetime.date.fromisoformat(last_live)).days


def _fmt(x):
    return f"{x:.1f}".rstrip("0").rstrip(".")


def render(template, ctx):
    return template.format(**ctx)


def pick_message(rule, handle, week):
    rnd = random.Random(f"{handle}|{rule['id']}|{week}")
    return rnd.choice(rule["messages"])


def _context(creator, st, config, today):
    goal = goal_for(creator, config)
    done = st["week_hours"] if st["week_hours"] is not None else 0.0
    return {"name": creators.first_name(creator["full_name"], creator["handle"]),
            "days": days_since(st["last_live"], today) if st["last_live"] else 0,
            "done": _fmt(done), "goal": _fmt(goal), "left": _fmt(max(goal - done, 0)),
            "pct": int(round(100 * done / goal)) if goal else 0}


def build_nudges(conn, config, today=None, now=None):
    """Queue new nudges for staff approval. Returns (new_nudges, notes). Guardrails: only active members, never creators on
    the skip list, never from stale numbers, one nudge per creator per week per rule, and a weekly cap."""
    today = today or datetime.date.today()
    notes = []
    age = stats_age_days(conn, now)
    if age is None:
        return [], ["No live stats have been uploaded yet."]
    if age > config.get("max_stats_age_days", 8):
        return [], [f"The live stats are {int(age)} days old. Upload a fresh file before nudges can run."]
    week = iso_week(today)
    weekly_missing = False
    new = []
    for c in creators.members(conn):
        h = c["handle"]
        st = conn.execute("SELECT * FROM live_stats WHERE handle=?", (h,)).fetchone()
        if st is None or is_skipped(conn, h, today):
            continue
        tt = conn.execute("SELECT tiktok_status FROM managed_creators WHERE handle=?", (h,)).fetchone()
        if tt and tt["tiktok_status"] == "terminated":
            continue
        sent_this_week = conn.execute("SELECT COUNT(*) FROM nudges WHERE handle=? AND week=?", (h, week)).fetchone()[0]
        ctx = _context(c, st, config, today)
        for rule in config["rules"]:
            if sent_this_week >= config.get("max_nudges_per_creator_per_week", 2):
                break
            fire = False
            if rule["type"] == "days_since_live":
                d = days_since(st["last_live"], today)
                fire = d is not None and d >= rule["min_days"]
            elif rule["type"] == "weekly_pct_below":
                if st["week_hours"] is None:
                    weekly_missing = True
                elif today.weekday() >= weekday_index(rule.get("from_weekday", "Thursday")):
                    fire = ctx_pct(st, c, config) < rule["pct"]
            if not fire:
                continue
            text = render(pick_message(rule, h, week), ctx)
            cur = conn.execute("INSERT OR IGNORE INTO nudges(handle, rule_id, week, text) VALUES (?,?,?,?)",
                               (h, rule["id"], week, text))
            if cur.rowcount:
                sent_this_week += 1
                new.append({"id": cur.lastrowid, "handle": h, "rule_id": rule["id"], "text": text})
    conn.commit()
    if weekly_missing:
        notes.append("The uploaded file has no \"this week\" hours column, so the weekly-goal rule was skipped.")
    return new, notes


def ctx_pct(st, creator, config):
    goal = goal_for(creator, config)
    return 100 * (st["week_hours"] or 0) / goal if goal else 100


def weekly_report(conn, config):
    """Group active members into hit goal / close / behind, from the latest uploaded stats."""
    close = config.get("close_pct", 70)
    out = {"hit": [], "close": [], "behind": [], "no_data": []}
    for c in creators.members(conn):
        st = conn.execute("SELECT * FROM live_stats WHERE handle=?", (c["handle"],)).fetchone()
        if st is None or st["week_hours"] is None:
            out["no_data"].append(c["handle"])
            continue
        pct = ctx_pct(st, c, config)
        item = (c["handle"], st["week_hours"], goal_for(c, config), int(round(pct)))
        out["hit" if pct >= 100 else "close" if pct >= close else "behind"].append(item)
    for k in ("hit", "close", "behind"):
        out[k].sort(key=lambda x: -x[3])
    return out


def mark_nudge(conn, nudge_id, status, channel=None, decided_by=None):
    conn.execute("UPDATE nudges SET status=?, channel=?, decided_by=? WHERE id=?",
                 (status, channel, decided_by, nudge_id))
    conn.commit()


def get_nudge(conn, nudge_id):
    return conn.execute("SELECT * FROM nudges WHERE id=?", (nudge_id,)).fetchone()
