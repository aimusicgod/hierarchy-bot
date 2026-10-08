"""Performance check-ins ("nudges"): read a TikTok LIVE Backstage CSV, apply the rules in nudge_rules.json,
and produce supportive messages for staff to approve. No Discord code, and the bot never touches Backstage logins."""
import calendar
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
        if r.get("type") not in ("days_since_live", "weekly_pct_below", "escalate_to_manager", "month_pace", "weekly_update"):
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


def duration_hours(v):
    """TikTok writes durations like "33h 27m 54s" or "38m 16s" or "0s"; a bare number is taken as hours."""
    t = str(v or "").strip().lower()
    if not t or t in ("-", "--"):
        return None
    m = re.fullmatch(r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", t)
    if m and any(m.groups()):
        d, h, mi, sec = (int(x or 0) for x in m.groups())
        return round(d * 24 + h + mi / 60 + sec / 3600, 4)
    return _num(t)


def _period(v):
    found = re.findall(r"(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    if not found:
        return None, None
    a = "-".join(found[0])
    return a, "-".join(found[-1])


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
        for c in cols:                      # allow "Valid go LIVE days in L30D (d)"-style unit suffixes, nothing else
            cl = c.lower()
            if any(cl.startswith(a) and re.match(r"^\s*[\(\[]", cl[len(a):]) for a in aliases[key]):
                return c
        return None

    c = {k: pick(k) for k in aliases}
    rows = []
    for _, r in body.iterrows():
        h = core.norm_handle(r[c["handle"]])
        if not h:
            continue
        ps, pe = _period(r[c["period"]]) if c.get("period") else (None, None)
        g = lambda k, f=_num: f(r[c[k]]) if c.get(k) else None
        rows.append({"handle": h, "creator_id": g("creator_id", lambda x: str(x).strip() or None),
                     "diamonds": g("diamonds"), "live_hours": g("live_hours", duration_hours),
                     "valid_days": g("valid_days"), "streams": g("streams"), "period_start": ps, "period_end": pe,
                     "diamonds_prev": g("diamonds_prev"), "hours_prev": g("hours_prev", duration_hours), "days_prev": g("days_prev"),
                     "join_date": g("join_time", _date), "manager": g("manager", lambda x: str(x).strip().lower() or None), "days_since_join": g("days_since_join"),
                     "last_live": _date(r[c["last_live"]]) if c["last_live"] else None,
                     "week_hours": _num(r[c["week_hours"]]) if c["week_hours"] else None,
                     "week_days": _num(r[c["week_days"]]) if c["week_days"] else None,
                     "hours_30d": _num(r[c["hours_30d"]]) if c["hours_30d"] else None,
                     "days_30d": _num(r[c["days_30d"]]) if c["days_30d"] else None})
    found = [k for k in aliases if c[k]]
    return rows, found


def save_stats(conn, rows, now=None):
    """Replace the stored numbers. TikTok's export has no "last LIVE" date, so when it's missing we work it out from the
    last upload: if streams or hours went up since then, they were LIVE up to this file's cutoff date. A creator with no
    LIVE at all this month (and nothing earlier on record) counts as quiet since the day before the month started."""
    now = (now or creators.utcnow()).isoformat()
    prev = {r["handle"]: r for r in conn.execute("SELECT * FROM live_stats")}
    conn.execute("DELETE FROM live_stats")
    for r in rows:
        last = r.get("last_live")
        if last is None:
            p = prev.get(r["handle"])
            end = r.get("period_end")
            grew = False
            if p is not None:
                grew = ((r.get("streams") or 0) > (p["streams"] or 0)) or ((r.get("live_hours") or 0) > (p["live_hours"] or 0) + 0.01) \
                    or (p["period_end"] and end and p["period_end"][:7] != end[:7] and (r.get("streams") or 0) > 0)
            if grew and end:
                last = end
            elif p is not None:
                last = p["last_live"]
            if last is None and r.get("streams") == 0 and r.get("period_start"):
                last = (datetime.date.fromisoformat(r["period_start"]) - datetime.timedelta(days=1)).isoformat()
        conn.execute("INSERT INTO live_stats(handle, last_live, week_hours, week_days, hours_30d, days_30d, uploaded_at, diamonds, "
                     "live_hours, valid_days, streams, period_start, period_end, diamonds_prev, hours_prev, days_prev, join_date, manager, days_since_join) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (r["handle"], last, r.get("week_hours"), r.get("week_days"), r.get("hours_30d"), r.get("days_30d"), now,
                      r.get("diamonds"), r.get("live_hours"), r.get("valid_days"), r.get("streams"), r.get("period_start"),
                      r.get("period_end"), r.get("diamonds_prev"), r.get("hours_prev"), r.get("days_prev"), r.get("join_date"), r.get("manager"), r.get("days_since_join")))
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


# ---------- monthly goals (8 valid days, 20 hours, 100 diamonds) ----------
MONTH_DEFAULTS = {"min_valid_days": 8, "min_hours": 20, "min_diamonds": 100, "max_realistic_hours_per_day": 5,
                  "behind_ratio_days": 0.4, "behind_ratio_hours_per_day": 1.0, "behind_from_day": 8,
                  "agency_active_floor_pct": 13, "agency_active_warn_pct": 25}


def goals(config):
    return {**MONTH_DEFAULTS, **config.get("month_goals", {})}


def month_status(st, config):
    """Where a creator stands on this month's minimums. Returns None if the file had no month-to-date numbers."""
    if st["valid_days"] is None or st["live_hours"] is None or not st["period_end"]:
        return None
    g = goals(config)
    end = datetime.date.fromisoformat(st["period_end"])
    remaining = calendar.monthrange(end.year, end.month)[1] - end.day          # days still to come after the data cutoff
    days_done, hours_done = st["valid_days"], st["live_hours"]
    need_days = max(g["min_valid_days"] - days_done, 0)
    need_hours = max(g["min_hours"] - hours_done, 0)
    diamonds = st["diamonds"] or 0
    if need_days == 0 and need_hours == 0:
        status = "met"
    elif need_days > remaining or need_hours > remaining * g["max_realistic_hours_per_day"] \
            or (remaining > 0 and need_days >= remaining and need_days > 0):
        status = "critical"                      # cannot make it, or has to go LIVE every single remaining day
    elif end.day >= g["behind_from_day"] and remaining > 0 and (need_days / remaining > g["behind_ratio_days"]
                                                                or need_hours / remaining > g["behind_ratio_hours_per_day"]):
        status = "behind"
    else:
        status = "ok"
    return {"status": status, "remaining": remaining, "days_done": days_done, "need_days": need_days, "hours_done": hours_done,
            "need_hours": need_hours, "diamonds": diamonds, "diamonds_goal": g["min_diamonds"],
            "diamonds_short": max(g["min_diamonds"] - diamonds, 0), "days_goal": g["min_valid_days"], "hours_goal": g["min_hours"],
            "period_end": end}


def agency_active(conn, config):
    """Share of the creators in the last upload who have reached the monthly active minimum (8 valid days), plus the best case
    if everyone who still can reach it does. Compared with the shut-down floor (13% by default)."""
    g = goals(config)
    total = met = possible = 0
    for st in conn.execute("SELECT * FROM live_stats").fetchall():
        ms = month_status(st, config)
        if ms is None:
            continue
        total += 1
        if ms["days_done"] >= g["min_valid_days"]:
            met += 1
            possible += 1
        elif ms["need_days"] <= ms["remaining"]:
            possible += 1
    if not total:
        return None
    best = 100.0 * possible / total
    level = "danger" if best <= g["agency_active_floor_pct"] else "watch" if best <= g["agency_active_warn_pct"] else "ok"
    return {"total": total, "met": met, "possible": possible, "now_pct": 100.0 * met / total, "best_pct": best, "level": level,
            "floor": g["agency_active_floor_pct"], "warn": g["agency_active_warn_pct"]}


def top_creators(conn, n=10):
    return conn.execute("SELECT handle, diamonds FROM live_stats WHERE diamonds > 0 ORDER BY diamonds DESC LIMIT ?", (n,)).fetchall()


def _context(creator, st, config, today):
    goal = goal_for(creator, config)
    done = st["week_hours"] if st["week_hours"] is not None else 0.0
    ctx = {"name": creators.first_name(creator["full_name"], creator["handle"]),
           "days": days_since(st["last_live"], today) if st["last_live"] else 0,
           "handle": creator["handle"], "last_live": st["last_live"] or "unknown",
           "done": _fmt(done), "goal": _fmt(goal), "left": _fmt(max(goal - done, 0)),
           "pct": int(round(100 * done / goal)) if goal else 0,
           "contact": config.get("manager_contact_line", "your manager")}
    ms = month_status(st, config)
    if ms:
        ctx.update({"diamonds": f"{int(ms['diamonds']):,}", "diamonds_goal": ms["diamonds_goal"], "diamonds_short": int(ms["diamonds_short"]),
                    "valid_days": _fmt(ms["days_done"]), "valid_days_goal": ms["days_goal"], "valid_days_left": _fmt(ms["need_days"]),
                    "month_hours": _fmt(ms["hours_done"]), "month_hours_goal": ms["hours_goal"], "month_hours_left": _fmt(ms["need_hours"]),
                    "days_remaining": ms["remaining"]})
    return ctx


def build_nudges(conn, config, today=None, now=None):
    """Queue new nudges for staff approval. Returns (new_nudges, notes). Guardrails: only active members, never creators on
    the skip list, never from stale numbers, one nudge per creator per week per rule, and a weekly cap. Staff-only alerts
    (manager follow-ups) and the weekly progress update are outside that cap."""
    today = today or datetime.date.today()
    notes = []
    age = stats_age_days(conn, now)
    if age is None:
        return [], ["No live stats have been uploaded yet."]
    if age > config.get("max_stats_age_days", 8):
        return [], [f"The live stats are {int(age)} days old. Upload a fresh file before nudges can run."]
    week = iso_week(today)
    weekly_missing = month_missing = False
    new = []
    for c in creators.members(conn):
        h = c["handle"]
        st = conn.execute("SELECT * FROM live_stats WHERE handle=?", (h,)).fetchone()
        if st is None or is_skipped(conn, h, today):
            continue
        tt = conn.execute("SELECT tiktok_status FROM managed_creators WHERE handle=?", (h,)).fetchone()
        if tt and tt["tiktok_status"] == "terminated":
            continue
        sent_this_week = conn.execute("SELECT COUNT(*) FROM nudges WHERE handle=? AND week=? AND rule_id != 'weekly_update' "
                                      "AND rule_id NOT LIKE 'manager_%'", (h, week)).fetchone()[0]
        ctx = _context(c, st, config, today)
        ms = month_status(st, config)
        for rule in config["rules"]:
            uncapped = rule["type"] in ("escalate_to_manager", "weekly_update")
            if not uncapped and sent_this_week >= config.get("max_nudges_per_creator_per_week", 2):
                continue
            fire = False
            if rule["type"] in ("days_since_live", "escalate_to_manager") and "status" not in rule:
                d = days_since(st["last_live"], today)
                fire = d is not None and d >= rule["min_days"]
            elif rule["type"] in ("escalate_to_manager", "month_pace"):
                if ms is None:
                    month_missing = True
                else:
                    fire = ms["status"] == rule["status"] and today.day >= rule.get("from_day", 1)
            elif rule["type"] == "weekly_update":
                if ms is None:
                    month_missing = True
                else:
                    fire = today.weekday() >= weekday_index(rule.get("from_weekday", "Monday"))
            elif rule["type"] == "weekly_pct_below":
                if st["week_hours"] is None:
                    weekly_missing = True
                elif today.weekday() >= weekday_index(rule.get("from_weekday", "Thursday")):
                    fire = ctx_pct(st, c, config) < rule["pct"]
            if not fire:
                continue
            try:
                text = render(pick_message(rule, h, week), ctx)
            except KeyError:                       # a rule uses a number this file doesn't have
                continue
            cur = conn.execute("INSERT OR IGNORE INTO nudges(handle, rule_id, week, text) VALUES (?,?,?,?)",
                               (h, rule["id"], week, text))
            if cur.rowcount:
                if not uncapped:
                    sent_this_week += 1
                new.append({"id": cur.lastrowid, "handle": h, "rule_id": rule["id"], "text": text})
    conn.commit()
    if weekly_missing:
        notes.append("The uploaded file has no \"this week\" hours column, so the weekly-goal rule was skipped.")
    if month_missing:
        notes.append("The uploaded file has no month-to-date Diamonds / LIVE duration / Valid go LIVE days columns, so the monthly rules were skipped.")
    return new, notes


def ctx_pct(st, creator, config):
    goal = goal_for(creator, config)
    return 100 * (st["week_hours"] or 0) / goal if goal else 100


def weekly_report(conn, config):
    """Group everyone in the latest upload by where they stand on this month's minimums."""
    out = {"met": [], "ok": [], "behind": [], "critical": [], "no_data": [], "low_diamonds": []}
    for st in conn.execute("SELECT * FROM live_stats ORDER BY handle").fetchall():
        ms = month_status(st, config)
        if ms is None:
            out["no_data"].append(st["handle"])
            continue
        out[ms["status"]].append((st["handle"], ms))
        if ms["diamonds_short"] > 0:
            out["low_diamonds"].append((st["handle"], ms))
    out["critical"].sort(key=lambda x: x[1]["need_days"], reverse=True)
    out["behind"].sort(key=lambda x: x[1]["need_days"], reverse=True)
    return out


def is_escalation(rule_id):
    return str(rule_id).startswith("manager_")


def mark_nudge(conn, nudge_id, status, channel=None, decided_by=None):
    conn.execute("UPDATE nudges SET status=?, channel=?, decided_by=? WHERE id=?",
                 (status, channel, decided_by, nudge_id))
    conn.commit()


def get_nudge(conn, nudge_id):
    return conn.execute("SELECT * FROM nudges WHERE id=?", (nudge_id,)).fetchone()
