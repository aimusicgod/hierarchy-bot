"""The unlock quota: a new member sees only a small part of the server until they have gone LIVE on enough valid days.
Pure logic (no Discord). The numbers come from the uploaded TikTok file, so it can only be as fresh as the last upload."""
import datetime

import core
import creators

DEFAULTS = {"enabled": True, "min_valid_days": 8, "min_hours": 0, "window_days": 30}


def rules(config):
    return {**DEFAULTS, **config.get("unlock", {})}


def state(conn, handle):
    return conn.execute("SELECT * FROM creator_unlock WHERE handle=?", (core.norm_handle(handle),)).fetchone()


def is_unlocked(conn, handle, config):
    if not rules(config)["enabled"]:
        return True
    r = state(conn, handle)
    return bool(r and r["unlocked_at"])


def mark_unlocked(conn, handle, by="auto"):
    h = core.norm_handle(handle)
    conn.execute("INSERT INTO creator_unlock(handle, unlocked_at, unlocked_by) VALUES (?,?,?) "
                 "ON CONFLICT(handle) DO UPDATE SET unlocked_at=excluded.unlocked_at, unlocked_by=excluded.unlocked_by",
                 (h, creators.iso(creators.utcnow()), str(by)))
    conn.commit()


def seed_existing(conn):
    """Once: everyone already approved keeps full access, so turning this on never locks out current members."""
    if core.get_setting(conn, "unlock:seeded"):
        return 0
    n = 0
    for c in creators.members(conn):
        if not (state(conn, c["handle"]) or {}):
            mark_unlocked(conn, c["handle"], "existing member")
            n += 1
    core.set_setting(conn, "unlock:seeded", "1")
    return n


def progress(conn, handle, config, today=None):
    """How close a member is to the quota. The window comes from TikTok's "Days since joining" (advanced to today, since the file is
    a few days old); without it we fall back to the day they were approved. Valid days: the better of this month so far and last month."""
    r = rules(config)
    today = today or datetime.date.today()
    st = conn.execute("SELECT * FROM live_stats WHERE handle=?", (core.norm_handle(handle),)).fetchone()
    in_days = None
    if st is not None and st["days_since_join"] is not None and st["period_end"]:
        in_days = int(st["days_since_join"]) + max((today - datetime.date.fromisoformat(st["period_end"])).days, 0)
    else:
        c = conn.execute("SELECT approved_at FROM creators WHERE handle=?", (core.norm_handle(handle),)).fetchone()
        if c and c["approved_at"]:
            in_days = (today - datetime.date.fromisoformat(str(c["approved_at"])[:10])).days
    days = max((st["valid_days"] or 0), (st["days_prev"] or 0)) if st else 0
    hours = max((st["live_hours"] or 0), (st["hours_prev"] or 0)) if st else 0
    return {"days": days, "hours": hours, "need_days": r["min_valid_days"], "need_hours": r["min_hours"], "has_data": st is not None,
            "days_in": in_days, "days_left": (r["window_days"] - in_days) if in_days is not None else None,
            "window_over": in_days is not None and in_days > r["window_days"],
            "met": st is not None and days >= r["min_valid_days"] and hours >= r["min_hours"]}


def evaluate(conn, config, today=None):
    """Unlock everyone who reached the quota; note who ran past the window without it (once). Returns {'unlocked': [], 'missed': []}."""
    out = {"unlocked": [], "missed": []}
    if not rules(config)["enabled"]:
        return out
    today = today or datetime.date.today()
    for c in creators.members(conn):
        h = c["handle"]
        st = state(conn, h)
        if st and st["unlocked_at"]:
            continue
        p = progress(conn, h, config, today)
        if p["met"]:
            mark_unlocked(conn, h, "quota")
            out["unlocked"].append(h)
        elif p["window_over"] and not (st and st["missed_at"]):
            conn.execute("INSERT INTO creator_unlock(handle, missed_at) VALUES (?,?) ON CONFLICT(handle) DO UPDATE SET missed_at=excluded.missed_at",
                         (h, creators.iso(creators.utcnow())))
            conn.commit()
            out["missed"].append((h, p))
    return out


def progress_text(conn, handle, config, today=None):
    """One friendly line for the member, or None when there's nothing to say."""
    r = rules(config)
    if not r["enabled"] or is_unlocked(conn, handle, config):
        return None
    p = progress(conn, handle, config, today)
    line = f"{int(p['days'])} of {p['need_days']} valid LIVE days"
    if p["need_hours"]:
        line += f" and {p['hours']:.0f} of {p['need_hours']} hours"
    if p["days_left"] is not None and p["days_left"] >= 0:
        line += f". {p['days_left']} days left to reach it"
    elif p["window_over"]:
        line += ". The 30 days are up, so message us in #support and a manager will help"
    return line + ". Reach it and the whole community and academy open up."
