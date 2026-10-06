"""Studio booking for artists: Hierarchy Music makes an introduction only. The bot never books and never takes payment,
and it never interprets a studio's reply: a person reads it and taps Available, Suggest other time or Not available."""
import json
import os

import creators

STUDIO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studios.json")
OPEN_STATUSES = ("new", "emailed", "offered", "booked")
MAX_OPEN = 2

DISCLAIMER = ("Hierarchy Music is making an introduction only. Booking, payment and terms are between you and the studio. "
              "You are responsible for paying the studio, and Hierarchy Music is not responsible for any unpaid bills or "
              "balances. Rates may change.")


class RequestLimit(Exception):
    pass


def load_studios(path=STUDIO_PATH):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ids = set()
    for s in data["studios"]:
        if not s.get("id") or not s.get("name"):
            raise ValueError("Every studio needs an id and a name.")
        if s["id"] in ids:
            raise ValueError(f"Studio id \"{s['id']}\" is used twice.")
        ids.add(s["id"])
        s.setdefault("rooms", [])
    return {s["id"]: s for s in data["studios"]}


def title(s):
    """Studio name with its city, e.g. 'Paramount Recording Studios (Los Angeles, CA)'."""
    return f"{s['name']} ({s['city']})" if s.get("city") else s["name"]


def ordered(studios):
    """Featured studios first, then the rest in file order."""
    return sorted(studios.values(), key=lambda s: not s.get("featured"))


def room_options(studios):
    """[(value, label)] for the room picker. A studio with no rooms listed gets one option."""
    out = []
    for s in ordered(studios):
        if s["rooms"]:
            out += [(f"{s['id']}|{r['name']}", f"{s['name']} ({s['city']}): {r['name']}" if s.get("city") else f"{s['name']}: {r['name']}") for r in s["rooms"]]
        else:
            out.append((f"{s['id']}|", title(s)))
    return out


def rate_lines(studio):
    """Plain-text rate card for one studio. Only shows what was entered; never fills in a number."""
    if not studio["rooms"]:
        return "Rates haven't been added yet. Pick this studio and we'll get current rates for you."
    lines = []
    for r in studio["rooms"]:
        bits = [r.get("rate") or "rate on request"]
        if r.get("minimum"):
            bits.append(f"minimum {r['minimum']}")
        line = f"**{r['name']}**: " + ", ".join(bits)
        if r.get("includes"):
            line += "\nIncludes: " + ", ".join(r["includes"])
        lines.append(line)
    lines.append(f"Rates as of {studio['rates_as_of']}." if studio.get("rates_as_of") else "Rates may change.")
    return "\n".join(lines)


def open_count(conn, discord_id):
    q = ",".join("?" * len(OPEN_STATUSES))
    return conn.execute(f"SELECT COUNT(*) FROM studio_requests WHERE discord_id=? AND status IN ({q})",
                        (str(discord_id), *OPEN_STATUSES)).fetchone()[0]


def create_request(conn, discord_id, studio_id, room, dates, length, needs):
    if open_count(conn, discord_id) >= MAX_OPEN:
        raise RequestLimit(f"You already have {MAX_OPEN} open studio requests. Once one is finished or closed, you can send another.")
    cur = conn.execute("INSERT INTO studio_requests(discord_id, studio_id, room, dates, length, needs) VALUES (?,?,?,?,?,?)",
                       (str(discord_id), studio_id, room or None, dates.strip(), length.strip(), (needs or "").strip()))
    conn.commit()
    return cur.lastrowid


def get_request(conn, req_id):
    return conn.execute("SELECT * FROM studio_requests WHERE id=?", (req_id,)).fetchone()


def update_request(conn, req_id, **fields):
    keys = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE studio_requests SET {keys} WHERE id=?", (*fields.values(), req_id))
    conn.commit()
    return get_request(conn, req_id)


def email_subject(req, artist_first):
    return f"[HM-BOOK-{req['id']}] Session request from {artist_first}: {req['dates'][:60]}"


def email_body(req, studio, artist_first):
    room = f"\nRoom: {req['room']}" if req["room"] else ""
    return (f"Hello {studio['name']},\n\n"
            f"An artist from the Hierarchy Music network, {artist_first}, would like to book a session.{room}\n"
            f"Preferred dates: {req['dates']}\n"
            f"Session length: {req['length']}\n"
            f"Engineer or gear needs: {req['needs'] or 'none listed'}\n\n"
            "Please reply to this email with your availability and booking details. "
            "A member of our team will read your reply and pass it on to the artist.\n\n"
            "Hierarchy Music is making an introduction only. The artist will book and pay you directly, and terms are between "
            "the artist and the studio.\n\nThank you,\nHierarchy Music\n")


def offer_text(studio, req, staff_note=None):
    contact = studio.get("booking_contact") or "the studio's booking contact (the team will send it to you)"
    wording = studio.get("referral_wording") or "Mention that you were referred by Hierarchy Music."
    note = f"\n\n**Suggested time from the studio:** {staff_note}" if staff_note else ""
    return (f"Good news! **{studio['name']}** can take your request{note}\n\n"
            f"**Booking contact:** {contact}\n**When you book:** {wording}\n\n"
            f"{DISCLAIMER}\n\nWhen you've booked, tap **I've booked** below so we have a record.")


def mark_booked(conn, req_id, date_iso):
    return update_request(conn, req_id, status="booked", booked_date=date_iso)


def due_followups(conn, today_iso):
    return conn.execute("SELECT * FROM studio_requests WHERE status='booked' AND booked_date < ? AND asked_at IS NULL",
                        (today_iso,)).fetchall()


def record_session(conn, req_id, happened):
    return update_request(conn, req_id, status="done", session_happened=int(bool(happened)))
