"""Username changes and history. A creator's TikTok username is the key everything hangs off (stats, payouts, check-ins), so a
rename moves every record to the new name in one step and keeps the old name on file. Instagram changes are tracked too.
No Discord code in here."""
import re

import core
import creators


def _handle_tables(conn):
    out = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
        if "handle" in cols:
            out.append(name)
    return out


def _now():
    return creators.iso(creators.utcnow())


def tiktok_exists(conn, handle):
    return any(conn.execute(f'SELECT 1 FROM "{t}" WHERE handle=? LIMIT 1', (handle,)).fetchone() for t in _handle_tables(conn))


def rename_tiktok(conn, old, new, by=None, source="staff"):
    """Move every record from the old TikTok username to the new one. All-or-nothing."""
    old, new = core.norm_handle(old), creators.clean_tiktok(new)
    if old == new:
        raise ValueError("That is already their username.")
    if not tiktok_exists(conn, old):
        raise ValueError(f"I don't have a creator called @{old}.")
    if tiktok_exists(conn, new):
        raise ValueError(f"@{new} is already on file as a different creator, so I can't rename onto it. "
                         "If they are the same person, remove the duplicate first.")
    try:
        for t in _handle_tables(conn):
            conn.execute(f'UPDATE "{t}" SET handle=? WHERE handle=?', (new, old))
        conn.execute("UPDATE creator_ids SET handle=? WHERE handle=?", (new, old))
        conn.execute("UPDATE handle_history SET creator_handle=? WHERE creator_handle=?", (new, old))
        conn.execute("INSERT INTO handle_history(platform, old_name, new_name, creator_handle, changed_at, changed_by, source) "
                     "VALUES ('tiktok',?,?,?,?,?,?)", (old, new, new, _now(), str(by) if by else None, source))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new


def rename_instagram(conn, tiktok_handle, new, by=None, source="staff"):
    """Change the Instagram username on file for a creator (and on the scout submission that found them)."""
    tt = core.norm_handle(tiktok_handle)
    row = conn.execute("SELECT ig_username FROM creators WHERE handle=?", (tt,)).fetchone()
    if row is None:
        raise ValueError(f"I don't have a creator called @{tt}.")
    old = row["ig_username"] or ""
    new = creators.clean_instagram(new)
    if old == new:
        raise ValueError("That is already their Instagram username.")
    clash = conn.execute("SELECT 1 FROM creators WHERE ig_username=? AND handle!=?", (new, tt)).fetchone() \
        or (old and conn.execute("SELECT 1 FROM submissions WHERE ig_username=? AND ig_username!=?", (new, old)).fetchone())
    if clash:
        raise ValueError(f"@{new} is already on file for someone else on Instagram.")
    try:
        conn.execute("UPDATE creators SET ig_username=? WHERE handle=?", (new, tt))
        if old:
            conn.execute("UPDATE submissions SET ig_username=?, ig_url=? WHERE ig_username=?",
                         (new, f"https://instagram.com/{new}", old))
        conn.execute("INSERT INTO handle_history(platform, old_name, new_name, creator_handle, changed_at, changed_by, source) "
                     "VALUES ('instagram',?,?,?,?,?,?)", (old or "(none)", new, tt, _now(), str(by) if by else None, source))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new


def previous_names(conn, handle):
    """Everything this person has been called, newest change first: [(platform, old, new, when, source), ...]."""
    h = current_handle(conn, handle)
    names = {h}
    rows = {}
    changed = True
    while changed:
        changed = False
        for r in conn.execute("SELECT * FROM handle_history WHERE platform='tiktok'").fetchall():
            if r["new_name"] in names and r["old_name"] not in names:
                names.add(r["old_name"])
                changed = True
    for r in conn.execute("SELECT * FROM handle_history ORDER BY id DESC").fetchall():
        if r["creator_handle"] in names or (r["platform"] == "tiktok" and (r["new_name"] in names or r["old_name"] in names)):
            rows[r["id"]] = r
    return [(r["platform"], r["old_name"], r["new_name"], r["changed_at"], r["source"]) for r in rows.values()]


def current_handle(conn, name):
    """Follow old -> new names to the current TikTok username (so an old name in a spreadsheet still finds the right person)."""
    h = core.norm_handle(name)
    for _ in range(20):
        r = conn.execute("SELECT new_name FROM handle_history WHERE platform='tiktok' AND old_name=? ORDER BY id DESC LIMIT 1", (h,)).fetchone()
        if not r or r["new_name"] == h:
            return h
        h = r["new_name"]
    return h


def apply_export_renames(conn, rows):
    """Called with the rows of an uploaded TikTok file. Uses the Creator ID to spot renames and records them.
    Returns (renamed [(old, new)], skipped [(old, new, why)]). Mutates rows so they use the current username."""
    renamed, skipped = [], []
    now = _now()
    for r in rows:
        cid = str(r.get("creator_id") or "").strip()
        h = r["handle"]
        known = conn.execute("SELECT handle FROM creator_ids WHERE creator_id=?", (cid,)).fetchone() if cid else None
        if known and known["handle"] != h:
            old = known["handle"]
            try:
                rename_tiktok(conn, old, h, by="export", source="TikTok export (same Creator ID)")
                renamed.append((old, h))
            except ValueError as e:
                if tiktok_exists(conn, old) and not tiktok_exists(conn, h):
                    skipped.append((old, h, str(e)))
                    r["handle"] = old
                else:
                    skipped.append((old, h, str(e)))
        elif not tiktok_exists(conn, h):
            cur = current_handle(conn, h)
            if cur != h and tiktok_exists(conn, cur):
                r["handle"] = cur                       # an old name in the file: file it under the current one
        if cid:
            conn.execute("INSERT INTO creator_ids(creator_id, handle, first_seen, last_seen) VALUES (?,?,?,?) "
                         "ON CONFLICT(creator_id) DO UPDATE SET handle=excluded.handle, last_seen=excluded.last_seen",
                         (cid, r["handle"], now, now))
    conn.commit()
    return renamed, skipped


# ---------- requests from creators (staff approve) ----------
def creator_by_discord(conn, discord_id):
    return conn.execute("SELECT * FROM creators WHERE discord_id=? AND member_status='member'", (str(discord_id),)).fetchone()


def new_request(conn, discord_id, platform, new_name):
    cr = creator_by_discord(conn, discord_id)
    if cr is None:
        raise ValueError("I can only change usernames for approved members.")
    if conn.execute("SELECT 1 FROM username_requests WHERE discord_id=? AND status='pending'", (str(discord_id),)).fetchone():
        raise ValueError("You already have a username change waiting for review.")
    if platform == "tiktok":
        new = creators.clean_tiktok(new_name)
        old = cr["handle"]
        if new == old:
            raise ValueError("That is already your TikTok username.")
        if tiktok_exists(conn, new):
            raise ValueError("That TikTok username is already on file for someone else. Ask staff for help.")
    else:
        new = creators.clean_instagram(new_name)
        old = cr["ig_username"] or "(none)"
        if new == cr["ig_username"]:
            raise ValueError("That is already your Instagram username.")
    cur = conn.execute("INSERT INTO username_requests(discord_id, platform, old_name, new_name) VALUES (?,?,?,?)",
                       (str(discord_id), platform, old, new))
    conn.commit()
    return conn.execute("SELECT * FROM username_requests WHERE id=?", (cur.lastrowid,)).fetchone()


def decide_request(conn, req_id, approve, by):
    req = conn.execute("SELECT * FROM username_requests WHERE id=?", (req_id,)).fetchone()
    if req is None or req["status"] != "pending":
        raise ValueError("That request was already handled.")
    if approve:
        if req["platform"] == "tiktok":
            rename_tiktok(conn, req["old_name"], req["new_name"], by=by, source="creator request")
        else:
            cr = creator_by_discord(conn, req["discord_id"])
            if cr is None:
                raise ValueError("That creator is no longer an active member.")
            rename_instagram(conn, cr["handle"], req["new_name"], by=by, source="creator request")
    conn.execute("UPDATE username_requests SET status=?, decided_by=? WHERE id=?",
                 ("approved" if approve else "denied", str(by), req_id))
    conn.commit()
    return conn.execute("SELECT * FROM username_requests WHERE id=?", (req_id,)).fetchone()
