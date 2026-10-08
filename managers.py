"""Per-manager performance: the share of each manager's creators who reached the monthly active minimum. Staff-only numbers.
It reports and records; it never takes action on anyone."""
import datetime

import nudges

DEFAULTS = {"warn_pct": 40, "fire_pct": 30, "min_creators": 5, "min_tenure_days": 30, "owner_emails": [], "names": {}}


def rules(config):
    return {**DEFAULTS, **config.get("manager_rules", {})}


def report(conn, config):
    """One entry per manager found in the last upload, worst first. level: ok | watch | danger | small | owner."""
    r = rules(config)
    owners = {e.lower() for e in r["owner_emails"]}
    groups = {}
    for st in conn.execute("SELECT * FROM live_stats").fetchall():
        key = (st["manager"] or "").strip().lower()
        ms = nudges.month_status(st, config)
        if not key or ms is None:
            continue
        end = ms["period_end"]
        if st["join_date"]:
            tenure = (end - datetime.date.fromisoformat(st["join_date"])).days
            if tenure < r["min_tenure_days"]:
                continue
        g = groups.setdefault(key, {"manager": key, "total": 0, "met": 0, "possible": 0, "remaining": ms["remaining"],
                                    "month": end.strftime("%Y%m"), "end": end})
        g["total"] += 1
        if ms["status"] == "met":
            g["met"] += 1
            g["possible"] += 1
        elif ms["status"] != "critical":
            g["possible"] += 1
    out = []
    for key, g in groups.items():
        g["label"] = r["names"].get(key, key)
        g["now_pct"] = 100.0 * g["met"] / g["total"]
        g["best_pct"] = 100.0 * g["possible"] / g["total"]
        g["final"] = g["remaining"] == 0
        # judged on the best case while the month is running (no one is flagged for something they can still fix)
        pct = g["now_pct"] if g["final"] else g["best_pct"]
        g["level"] = ("owner" if key in owners else "small" if g["total"] < r["min_creators"]
                      else "danger" if pct < r["fire_pct"] else "watch" if pct < r["warn_pct"] else "ok")
        g["prior"] = prior_results(conn, key, g["month"])
        out.append(g)
    order = {"danger": 0, "watch": 1, "ok": 2, "small": 3, "owner": 4}
    out.sort(key=lambda g: (order[g["level"]], g["best_pct"]))
    return out


def prior_results(conn, manager, before_month, n=3):
    rows = conn.execute("SELECT * FROM manager_results WHERE manager=? AND month<? ORDER BY month DESC LIMIT ?",
                        (manager, before_month, n)).fetchall()
    return [(x["month"], x["pct"], x["level"]) for x in rows]


def record_final(conn, entries):
    """Called after an upload: a finished month's results are saved once so repeat misses can be seen later."""
    n = 0
    for g in entries:
        if g["final"] and g["level"] not in ("owner", "small"):
            conn.execute("INSERT OR REPLACE INTO manager_results(manager, month, total, active, pct, level) VALUES (?,?,?,?,?,?)",
                         (g["manager"], g["month"], g["total"], g["met"], g["now_pct"], g["level"]))
            n += 1
    conn.commit()
    return n


def strikes(g):
    """Months in a row (this one included once it has finished) below the line."""
    run = 1 if g["final"] and g["level"] == "danger" else 0
    for _, _, level in g["prior"]:
        if level == "danger":
            run += 1
        else:
            break
    return run


def line(g, with_history=True):
    icon = {"danger": "🚨", "watch": "⚠️", "ok": "✅", "small": "·", "owner": "·"}[g["level"]]
    s = f"{icon} **{g['label']}**: {g['met']} of {g['total']} active ({g['now_pct']:.0f}%)"
    if not g["final"]:
        s += f", best case {g['best_pct']:.0f}%"
    if g["level"] == "small":
        s += " (too few creators to judge)"
    elif g["level"] == "owner":
        s += " (you)"
    if with_history and g["prior"]:
        s += "\n   Earlier: " + ", ".join(f"{m[:4]}-{m[4:]} {p:.0f}%" for m, p, _ in g["prior"])
    k = strikes(g)
    if k >= 1 and g["level"] == "danger":
        s += f"\n   Below the line {k} month{'s' if k != 1 else ''} in a row"
    return s
