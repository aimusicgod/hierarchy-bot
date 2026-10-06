"""Academy: lessons from JSON files, quizzes, progress and module unlocks. No Discord code."""
import datetime
import json
import os
import re

import creators

LESSON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lessons")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,59}$")
RETAKE_AFTER = datetime.timedelta(hours=1)
DEFAULT_PASS = 70


def validate(lesson, path="lesson"):
    for key in ("id", "module", "title", "summary"):
        if lesson.get(key) in (None, ""):
            raise ValueError(f"{path}: missing \"{key}\".")
    if not _ID.match(str(lesson["id"])):
        raise ValueError(f"{path}: id must be lowercase letters, numbers, - or _ (max 60).")
    if not isinstance(lesson["module"], int) or lesson["module"] < 1:
        raise ValueError(f"{path}: module must be a whole number starting at 1.")
    for field in ("images", "checklist"):
        if field in lesson and not isinstance(lesson[field], list):
            raise ValueError(f"{path}: \"{field}\" must be a list.")
    q = lesson.get("quiz")
    if q:
        qs = q.get("questions") or []
        if not qs:
            raise ValueError(f"{path}: the quiz needs at least one question.")
        for i, item in enumerate(qs, 1):
            opts = item.get("options") or []
            if not item.get("q") or not 2 <= len(opts) <= 5:
                raise ValueError(f"{path}: question {i} needs text and 2 to 5 options.")
            if not isinstance(item.get("answer"), int) or not 0 <= item["answer"] < len(opts):
                raise ValueError(f"{path}: question {i}: \"answer\" must be the number of the right option, starting at 0.")
    return lesson


def load_lessons(directory=LESSON_DIR):
    out = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            lesson = validate(json.load(f), name)
        if lesson["id"] in out:
            raise ValueError(f"{name}: lesson id \"{lesson['id']}\" is used twice.")
        lesson.setdefault("order", 1)
        out[lesson["id"]] = lesson
    return out


def by_module(lessons):
    mods = {}
    for l in lessons.values():
        mods.setdefault(l["module"], []).append(l)
    for m in mods.values():
        m.sort(key=lambda x: (x["order"], x["id"]))
    return dict(sorted(mods.items()))


def pass_mark(lesson):
    return int((lesson.get("quiz") or {}).get("pass_percent", DEFAULT_PASS))


def grade(lesson, answers):
    """answers: list of chosen option indexes, one per question -> (score_pct, passed)"""
    qs = lesson["quiz"]["questions"]
    right = sum(1 for q, a in zip(qs, answers) if a == q["answer"])
    pct = int(round(100 * right / len(qs)))
    return pct, pct >= pass_mark(lesson)


def retake_wait(conn, discord_id, lesson_id, now=None):
    """Minutes until a failed quiz can be retaken (0 if they can take it now)."""
    now = now or creators.utcnow()
    r = conn.execute("SELECT passed, taken_at FROM quiz_attempts WHERE discord_id=? AND lesson_id=? "
                     "ORDER BY id DESC LIMIT 1", (str(discord_id), lesson_id)).fetchone()
    if not r or r["passed"]:
        return 0
    left = (creators.parse_iso(r["taken_at"]) + RETAKE_AFTER - now).total_seconds()
    return 0 if left <= 0 else int(-(-left // 60))


def record_attempt(conn, discord_id, lesson, score_pct, passed, now=None):
    now = (now or creators.utcnow()).isoformat()
    conn.execute("INSERT INTO quiz_attempts(discord_id, lesson_id, score_pct, passed, taken_at) VALUES (?,?,?,?,?)",
                 (str(discord_id), lesson["id"], score_pct, int(passed), now))
    if passed:
        conn.execute("INSERT INTO lesson_progress(discord_id, lesson_id, passed_at) VALUES (?,?,?) "
                     "ON CONFLICT(discord_id, lesson_id) DO UPDATE SET passed_at=COALESCE(passed_at, excluded.passed_at)",
                     (str(discord_id), lesson["id"], now))
    conn.commit()


def mark_complete(conn, discord_id, lesson_id, now=None):
    now = (now or creators.utcnow()).isoformat()
    conn.execute("INSERT INTO lesson_progress(discord_id, lesson_id, completed_at) VALUES (?,?,?) "
                 "ON CONFLICT(discord_id, lesson_id) DO UPDATE SET completed_at=COALESCE(completed_at, excluded.completed_at)",
                 (str(discord_id), lesson_id, now))
    conn.commit()


def lesson_done(conn, discord_id, lesson):
    r = conn.execute("SELECT completed_at, passed_at FROM lesson_progress WHERE discord_id=? AND lesson_id=?",
                     (str(discord_id), lesson["id"])).fetchone()
    if not r:
        return False
    return bool(r["passed_at"]) if lesson.get("quiz") else bool(r["completed_at"] or r["passed_at"])


def module_complete(conn, discord_id, module_lessons):
    return bool(module_lessons) and all(lesson_done(conn, discord_id, l) for l in module_lessons)


def previous_module(lessons, module):
    lower = [m for m in by_module(lessons) if m < module]
    return max(lower) if lower else None


def module_unlocked(conn, discord_id, lessons, module):
    prev = previous_module(lessons, module)
    return prev is None or module_complete(conn, discord_id, by_module(lessons)[prev])


def award_module(conn, discord_id, module, now=None):
    """True the first time a module is awarded (so the congratulation is only posted once)."""
    cur = conn.execute("INSERT OR IGNORE INTO module_awards(discord_id, module, at) VALUES (?,?,?)",
                       (str(discord_id), module, (now or creators.utcnow()).isoformat()))
    conn.commit()
    return cur.rowcount == 1


def progress(conn, discord_id, lessons):
    out = []
    for m, items in by_module(lessons).items():
        out.append({"module": m, "complete": module_complete(conn, discord_id, items),
                    "unlocked": module_unlocked(conn, discord_id, lessons, m),
                    "lessons": [(l, lesson_done(conn, discord_id, l)) for l in items]})
    return out


def post_record(conn, lesson_id):
    return conn.execute("SELECT * FROM lesson_posts WHERE lesson_id=?", (lesson_id,)).fetchone()


def save_post(conn, lesson_id, channel_id, thread_id, message_id):
    conn.execute("INSERT OR REPLACE INTO lesson_posts(lesson_id, channel_id, thread_id, message_id) VALUES (?,?,?,?)",
                 (lesson_id, str(channel_id), str(thread_id), str(message_id)))
    conn.commit()
