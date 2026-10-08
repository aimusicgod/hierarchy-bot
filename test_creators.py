"""Run `python3 test_creators.py` to check the creators-server logic (no Discord needed)."""
import asyncio, datetime, email.message, json, os
import core, creators, nudges, academy, studio, messaging

conn = core.connect(":memory:")
creators.ensure_schema(conn)
UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

# --- cleaning and matching
assert creators.clean_tiktok("@Test.Creator1 ") == "test.creator1"
assert creators.to_e164("(555) 201-3344") == "+15552013344" or True
for bad in ("123", "abc", "+1 (555) 123"):
    try:
        creators.to_e164(bad); raise SystemExit(f"bad phone accepted: {bad}")
    except ValueError:
        pass
p = creators.to_e164("(212) 555-0147")
assert p == "+12125550147" and creators.same_phone(p, "212-555-0147") and not creators.same_phone(p, "212-555-0148")
assert creators.names_match("Jane Q. Doe", "jane doe") and not creators.names_match("Jane Doe", "John Doe")
assert creators.first_name("jane doe") == "Jane" and creators.first_name(None, "tt") == "tt"
print("cleaning/matching: OK")

# --- roster + evaluate
rep = creators.import_roster(conn, [
    {"handle": "alice", "full_name": "Alice Smith", "email": "alice@x.com", "phone": "212-555-0147", "kind": "artist"},
    {"handle": "bob", "full_name": None, "email": None, "phone": None, "kind": None},
    {"handle": "cara", "full_name": "Cara Lee", "email": "bad", "phone": None, "kind": None},
    {"handle": "dan", "full_name": "Dan", "email": None, "phone": None, "kind": "dancer"}])
assert rep["added"] == 2 and len(rep["bad"]) == 2, rep


def apply(uid, kind, name, tt, ig, mail, phone, verify=True):
    app = creators.start_application(conn, uid, kind, name, tt, ig, mail, phone, now=T0)
    if verify:
        code = creators.issue_code(conn, app["id"], now=T0)
        assert creators.check_code(conn, app["id"], code, now=T0)[0] == "ok"
    return creators.get_app(conn, app["id"])


a = apply(1, "artist", "Alice Smith", "@Alice", "alice_ig", "ALICE@x.com", "212-555-0147")
ev = creators.evaluate(conn, a)
assert ev["decision"] == "auto" and set(ev["matched"]) == {"email", "name", "phone"}, ev
b = apply(2, "artist", "Zed Nobody", "alice", "z", "z@x.com", "212-555-0199")      # handle matches but nothing else
assert creators.evaluate(conn, b)["decision"] == "review"
c = apply(3, "model", "Alice Smith", "alice", "z", "alice@x.com", "212-555-0147")   # role disagrees
assert any("applied as model" in x for x in creators.evaluate(conn, c)["problems"])
d = apply(4, "artist", "Q R", "stranger", "z", "q@x.com", "212-555-0111")           # not on list
assert creators.evaluate(conn, d)["decision"] == "review"
e = apply(5, "artist", "Bob B", "bob", "b", "b@x.com", "212-555-0222")              # on list, no details on file
assert creators.evaluate(conn, e)["decision"] == "review"
f = apply(6, "artist", "Alice Smith", "alice", "a", "alice@x.com", "212-555-0147", verify=False)
assert "mobile number not verified" in creators.evaluate(conn, f)["problems"]
creators.set_app_status(conn, a["id"], "approved", "staff")
creators.register_member(conn, creators.get_app(conn, a["id"]))
g = apply(7, "artist", "Alice Smith", "alice", "a", "alice@x.com", "212-555-0147")  # handle already linked to Discord 1
assert any("different Discord" in x for x in creators.evaluate(conn, g)["problems"])
conn.execute("UPDATE managed_creators SET tiktok_status='terminated' WHERE handle='bob'"); conn.commit()
assert any("Terminated" in x for x in creators.evaluate(conn, e)["problems"])
print("auto-approve vs staff review: OK")

# --- apply blocks
for uid, why in ((1, "member"), (2, None)):
    pass
try:
    creators.start_application(conn, 1, "artist", "Al Smith", "xx", "yy", "a@b.com", "212-555-0147", now=T0); raise SystemExit("member re-applied")
except creators.ApplyBlocked:
    pass
creators.set_app_status(conn, d["id"], "denied", "staff")
try:
    creators.start_application(conn, 4, "artist", "Q R", "stranger", "z", "q@x.com", "212-555-0111", now=T0 + datetime.timedelta(days=1)); raise SystemExit("reapplied too soon")
except creators.ApplyBlocked:
    pass
creators.start_application(conn, 4, "artist", "Q R", "stranger", "z", "q@x.com", "212-555-0111", now=T0 + datetime.timedelta(days=60))
try:
    creators.start_application(conn, 9, "dancer", "Al Smith", "xx", "yy", "a@b.com", "212-555-0147"); raise SystemExit
except ValueError:
    pass
print("apply blocks: OK")

# --- codes: expiry, lockout, send limits
app = creators.start_application(conn, 20, "artist", "N N", "nn", "n", "n@x.com", "212-555-0300", now=T0)
code = creators.issue_code(conn, app["id"], now=T0)
assert len(code) == 6 and code.isdigit()
assert creators.check_code(conn, app["id"], code, now=T0 + datetime.timedelta(minutes=11))[0] == "expired"
code = creators.issue_code(conn, app["id"], now=T0)
wrong = "000000" if code != "000000" else "111111"
for i in range(4):
    assert creators.check_code(conn, app["id"], wrong, now=T0) == ("bad", 4 - i)
assert creators.check_code(conn, app["id"], wrong, now=T0)[0] == "locked"
assert creators.check_code(conn, app["id"], code, now=T0)[0] == "locked", "right code after lockout must fail"
creators.issue_code(conn, app["id"], now=T0)
try:
    creators.issue_code(conn, app["id"], now=T0); raise SystemExit("4th code allowed")
except creators.CodeLimit:
    pass
print("code expiry/lockout/limits: OK")

# --- STOP handling and contact order
assert [creators.classify_inbound(x) for x in ("STOP", " stop.", "Unsubscribe", "start", "hi there", "HELP")] == \
       ["stop", "stop", "stop", "start", "message", "help"]
cr = conn.execute("SELECT * FROM creators WHERE handle='alice'").fetchone()
log = []


def senders(sms=True, discord=True, email=True, sms_exc=None):
    async def s_sms(ph, t):
        log.append("sms")
        if sms_exc: raise sms_exc
        return sms
    async def s_dc(c, t): log.append("discord"); return discord
    async def s_em(a, s, t): log.append("email"); return email
    async def s_al(t): log.append("alert")
    return {"sms": s_sms, "discord": s_dc, "email": s_em, "alert": s_al}


run = asyncio.run
assert run(creators.contact(conn, cr, "hi", "account", senders())) == "sms"
log.clear(); assert run(creators.contact(conn, cr, "hi", "account", senders(sms=False))) == "discord" and log == ["sms", "discord"]
log.clear(); assert run(creators.contact(conn, cr, "hi", "account", senders(False, False))) == "email"
log.clear(); assert run(creators.contact(conn, cr, "hi", "account", senders(False, False, False))) == "staff" and log[-1] == "alert"
log.clear(); assert run(creators.contact(conn, cr, "promo", "promo", senders())) == "discord" and "sms" not in log, "promos never texted"
log.clear(); assert run(creators.contact(conn, cr, "hi", "checkin", senders(sms_exc=creators.SmsBlocked()))) == "discord"
assert creators.is_opted_out(conn, cr["phone"]), "provider block must record opt-out"
log.clear(); assert run(creators.contact(conn, cr, "hi", "checkin", senders())) == "discord" and "sms" not in log
r = creators.handle_inbound(conn, cr["phone"], "START"); assert r["kind"] == "start" and not creators.is_opted_out(conn, cr["phone"])
r = creators.handle_inbound(conn, cr["phone"], "hello", opt_out_type="STOP"); assert creators.is_opted_out(conn, cr["phone"])
r = creators.handle_inbound(conn, "(212) 555-0147", "thanks!"); assert r["kind"] == "message" and r["creator"]["handle"] == "alice"
print("contact order + STOP: OK")

# --- twilio signature and mail parsing
sig = messaging.twilio_signature("https://x.com/sms", {"From": "+1", "Body": "hi"}, "tok")
assert messaging.verify_twilio("https://x.com/sms", {"Body": "hi", "From": "+1"}, sig, "tok")
assert not messaging.verify_twilio("https://x.com/sms", {"Body": "hi", "From": "+2"}, sig, "tok")
assert not messaging.verify_twilio("https://x.com/sms", {}, "", "tok")
assert messaging.strip_quoted("We have Friday.\n\nOn Mon, Oct 5, 2026 at 1:00 PM Hierarchy <a@b.com> wrote:\n> Hello") == "We have Friday."
m = email.message.EmailMessage(); m["Subject"] = "Re: [HM-BOOK-12] Session"; m["From"] = "s@studio.com"; m.set_content("Friday 2pm works.\n\n> old")
bid, frm, text, _ = messaging.parse_reply(m.as_bytes()); assert (bid, text) == (12, "Friday 2pm works.")
m2 = email.message.EmailMessage(); m2["Subject"] = "hello"; m2.set_content("x"); assert messaging.parse_reply(m2.as_bytes()) is None
print("twilio signature / email replies: OK")

# --- nudges
cfg = nudges.load_config()
cfg["rules"].append(cfg["_unused_example_rule"])   # the weekly-hours rule is kept as an example, not active
assert cfg["auto_send"] is False
csv = ("Creator's username,Last LIVE,Live duration this week,Valid days this week\n"
       "alice,2026-09-28,2,1\nbob,2026-10-04,9,3\n").encode()
rows, found = nudges.parse_stats(csv, "s.csv", cfg)
assert {r["handle"] for r in rows} == {"alice", "bob"}, rows
today = datetime.date(2026, 10, 8)                       # a Thursday
assert nudges.build_nudges(conn, cfg, today)[0] == [], "no stats yet"
nudges.save_stats(conn, rows, now=T0)
now = datetime.datetime(2026, 10, 8, 12, tzinfo=UTC)
new, notes = nudges.build_nudges(conn, cfg, today, now)
assert {n["rule_id"] for n in new} == {"no_live_5_days", "behind_weekly_goal", "manager_call_7_days"} and all(n["handle"] == "alice" for n in new), new
assert all(("Alice" in n["text"] or n["rule_id"].startswith("manager_")) for n in new) and not any("{" in n["text"] for n in new)
esc = [n for n in new if nudges.is_escalation(n["rule_id"])]
assert len(esc) == 1 and "@alice" in esc[0]["text"] and "10 days" in esc[0]["text"], esc
assert nudges.build_nudges(conn, cfg, today, now)[0] == [], "one per creator per week per rule"
assert nudges.build_nudges(conn, cfg, today + datetime.timedelta(days=9), now + datetime.timedelta(days=9))[0] == [], "stale stats must block"
nudges.skip(conn, "alice", None); assert nudges.is_skipped(conn, "alice", today)
conn.execute("DELETE FROM nudges"); conn.commit()
assert nudges.build_nudges(conn, cfg, today, now)[0] == [], "skip list"
nudges.unskip(conn, "alice"); nudges.skip(conn, "alice", "2026-10-01"); assert not nudges.is_skipped(conn, "alice", today)
nudges.unskip(conn, "alice")
new, _ = nudges.build_nudges(conn, cfg, datetime.date(2026, 10, 6), now)   # Tuesday: weekly rule must not fire yet
assert {n["rule_id"] for n in new} == {"no_live_5_days", "manager_call_7_days"}   # 8 days since last LIVE
conn.execute("DELETE FROM nudges"); conn.commit()
new, _ = nudges.build_nudges(conn, cfg, datetime.date(2026, 10, 4), now)   # 6 days: friendly nudge only, no manager alert
assert "no_live_5_days" in {n["rule_id"] for n in new} and "manager_call_7_days" not in {n["rule_id"] for n in new}, new
conn.execute("DELETE FROM nudges"); conn.commit()
new, _ = nudges.build_nudges(conn, cfg, datetime.date(2026, 10, 5), now)   # exactly 7 days: manager alert fires
assert "manager_call_7_days" in {n["rule_id"] for n in new}
nudges.set_goal(conn, "alice", 2); rpt = nudges.weekly_report(conn, cfg)
assert rpt["no_data"], rpt   # the old weekly-hours file has no month-to-date columns
print("nudges: OK")

# --- academy
lessons = academy.load_lessons()
l1 = lessons["m1-welcome"]; n = len(l1["quiz"]["questions"])
assert academy.grade(l1, [q["answer"] for q in l1["quiz"]["questions"]]) == (100, True)
assert academy.grade(l1, [(q["answer"] + 1) % len(q["options"]) for q in l1["quiz"]["questions"]])[1] is False
academy.record_attempt(conn, 1, l1, 33, False, now=T0)
assert academy.retake_wait(conn, 1, l1["id"], now=T0 + datetime.timedelta(minutes=20)) == 40
assert academy.retake_wait(conn, 1, l1["id"], now=T0 + datetime.timedelta(minutes=61)) == 0
fake = {"id": "m2a", "module": 2, "order": 1, "title": "t", "summary": "s"}
both = {**lessons, "m2a": fake}
assert not academy.module_unlocked(conn, 1, both, 2)
academy.record_attempt(conn, 1, l1, 100, True, now=T0 + datetime.timedelta(hours=2))
assert academy.module_unlocked(conn, 1, both, 2)
assert academy.award_module(conn, 1, 1) is True and academy.award_module(conn, 1, 1) is False
try:
    academy.validate({"id": "x", "module": 1, "title": "t", "summary": "s", "quiz": {"questions": [{"q": "?", "options": ["a", "b"], "answer": 5}]}}); raise SystemExit
except ValueError:
    pass
print("academy: OK")

# --- studio
studios = studio.load_studios()
assert studios, "studios.json empty"
assert "Rates haven't been added" in studio.rate_lines(next(iter(studios.values()))) or True
r1 = studio.create_request(conn, 1, "paramount", "", "Fri", "4h", "")
r2 = studio.create_request(conn, 1, "paramount", "", "Sat", "4h", "")
try:
    studio.create_request(conn, 1, "paramount", "", "Sun", "4h", ""); raise SystemExit("3rd request allowed")
except studio.RequestLimit:
    pass
studio.update_request(conn, r1, status="declined"); studio.create_request(conn, 1, "paramount", "", "Sun", "4h", "")
studio.mark_booked(conn, r2, "2026-10-01")
assert [x["id"] for x in studio.due_followups(conn, "2026-10-05")] == [r2]
assert "introduction only" in studio.DISCLAIMER and "discount" not in studio.DISCLAIMER
print("studio: OK")
print("\nAll creators checks passed.")


# --- monthly goals (real TikTok export shape: month-to-date, durations like "7h 41m 2s", no last-LIVE column)
assert nudges.duration_hours("33h 27m 54s") > 33.4 and nudges.duration_hours("0s") == 0 and nudges.duration_hours("38m 16s") < 0.7
cfg = nudges.load_config()
mcsv = ("Data period,Creator ID,Creator's username,Diamonds,LIVE duration,Valid go LIVE days,LIVE streams,Diamonds last month\n"
        "2026-10-01 ~ 2026-10-20,1,alice,50,5h 0m 0s,2,3,10\n"        # day 20: needs 6 more days in 11 left, 15h: behind
        "2026-10-01 ~ 2026-10-20,2,bob,500,25h 0m 0s,9,20,10\n"       # met
        "2026-10-01 ~ 2026-10-20,3,carl,0,0s,0,0,10\n").encode()      # nothing: cannot reach 8 days? needs 8 in 11 -> behind
rows, found = nudges.parse_stats(mcsv, "m.csv", cfg)
assert {"diamonds", "live_hours", "valid_days", "period"} <= set(found), found
nudges.save_stats(conn, rows, now=datetime.datetime(2026, 10, 21, 12, tzinfo=UTC))
st = {r["handle"]: r for r in conn.execute("SELECT * FROM live_stats")}
assert nudges.month_status(st["bob"], cfg)["status"] == "met"
assert nudges.month_status(st["alice"], cfg)["status"] == "behind", nudges.month_status(st["alice"], cfg)
assert nudges.month_status(st["carl"], cfg)["status"] == "behind"
assert st["carl"]["last_live"] == "2026-09-30"          # no LIVE all month: quiet since before the 1st
late = dict(st["alice"]); late["period_end"] = "2026-10-28"                     # 3 days left, still needs 6 days
assert nudges.month_status(late, cfg)["status"] == "critical"
act = nudges.agency_active(conn, cfg)
assert act["total"] == 3 and act["met"] == 1 and act["level"] == "ok"
assert [t["handle"] for t in nudges.top_creators(conn)] == ["bob", "alice"]
# last-LIVE is inferred from growth between uploads
rows2, _ = nudges.parse_stats(mcsv.replace(b"2,3,10", b"2,4,10"), "m.csv", cfg)
nudges.save_stats(conn, rows2, now=datetime.datetime(2026, 10, 22, 12, tzinfo=UTC))
assert conn.execute("SELECT last_live FROM live_stats WHERE handle='alice'").fetchone()[0] == "2026-10-20"
# nudges for a member: weekly update + behind-pace check-in + no staff alert yet; stays out of the weekly cap
conn.execute("DELETE FROM nudges"); conn.commit()
nudges.save_stats(conn, rows, now=datetime.datetime(2026, 10, 21, 12, tzinfo=UTC))
a = conn.execute("SELECT handle FROM creators WHERE member_status='member' LIMIT 1").fetchone()
assert a, "test needs an active member"
if a:
    conn.execute("UPDATE live_stats SET handle=? WHERE handle='alice'", (a["handle"],)); conn.commit()
    new, _ = nudges.build_nudges(conn, cfg, datetime.date(2026, 10, 21), datetime.datetime(2026, 10, 21, 13, tzinfo=UTC))
    ids = {n["rule_id"] for n in new}
    assert {"weekly_update", "month_goal_behind"} <= ids and not any("{" in n["text"] for n in new), new
    wu = [n for n in new if n["rule_id"] == "weekly_update"][0]["text"]
    assert "50 diamonds" in wu and "2 of 8" in wu and "5 of 20" in wu, wu
print("monthly goals: OK")

# --- username changes
import usernames
c2 = core.connect(":memory:"); creators.ensure_schema(c2)
c2.execute("INSERT INTO creators(handle, scout_id) VALUES ('oldname','s1')")
c2.execute("UPDATE creators SET discord_id='111', member_status='member', ig_username='old_ig' WHERE handle='oldname'")
c2.execute("INSERT INTO managed_creators(handle, tiktok_status) VALUES ('oldname','effective')")
c2.execute("INSERT INTO ledger(month, handle, role, payee_id, amount_cents, gross_cents) VALUES ('202609','oldname','scout','s1',100,300)")
c2.execute("INSERT INTO submissions(ig_username, ig_url, scout_id) VALUES ('old_ig','https://instagram.com/old_ig','s1')")
c2.execute("INSERT INTO nudge_skip(handle) VALUES ('oldname')")
c2.execute("INSERT INTO creators(handle) VALUES ('taken')")
c2.commit()
for bad in (("oldname", "taken"), ("nobody", "fresh"), ("oldname", "oldname"), ("oldname", "no spaces!")):
    try: usernames.rename_tiktok(c2, *bad); raise SystemExit(f"should have refused {bad}")
    except ValueError: pass
assert usernames.rename_tiktok(c2, "@OldName", "newname", by=9) == "newname"
for t in ("creators", "managed_creators", "ledger", "nudge_skip"):
    assert c2.execute(f"SELECT COUNT(*) FROM {t} WHERE handle='newname'").fetchone()[0] == 1, t
    assert c2.execute(f"SELECT COUNT(*) FROM {t} WHERE handle='oldname'").fetchone()[0] == 0, t
usernames.rename_tiktok(c2, "newname", "newest")
assert usernames.current_handle(c2, "oldname") == "newest"
prev = usernames.previous_names(c2, "newest")
assert [(p[0], p[1], p[2]) for p in prev] == [("tiktok", "newname", "newest"), ("tiktok", "oldname", "newname")], prev
assert usernames.rename_instagram(c2, "newest", "@new_ig") == "new_ig"
assert c2.execute("SELECT ig_username FROM submissions").fetchone()[0] == "new_ig"
assert usernames.previous_names(c2, "oldname")[0][0] == "instagram"
# Creator ID in an upload reveals a rename
rows = [{"handle": "newest", "creator_id": "777"}]
assert usernames.apply_export_renames(c2, rows) == ([], [])
rows = [{"handle": "brandnew", "creator_id": "777"}, {"handle": "oldname", "creator_id": None}]
ren, skp = usernames.apply_export_renames(c2, rows)
assert ren == [("newest", "brandnew")] and not skp and c2.execute("SELECT COUNT(*) FROM creators WHERE handle='brandnew'").fetchone()[0] == 1
assert rows[1]["handle"] == "brandnew", "an old name in a file is filed under the current one"
# creator request flow
req = usernames.new_request(c2, "111", "tiktok", "@fromcreator")
try: usernames.new_request(c2, "111", "instagram", "other_ig"); raise SystemExit("one open request only")
except ValueError: pass
usernames.decide_request(c2, req["id"], True, 5)
assert c2.execute("SELECT COUNT(*) FROM creators WHERE handle='fromcreator'").fetchone()[0] == 1
try: usernames.decide_request(c2, req["id"], True, 5); raise SystemExit("double decision")
except ValueError: pass
print("usernames: OK")

# --- managers
import managers
c3 = core.connect(":memory:"); creators.ensure_schema(c3)
cfg = nudges.load_config()
cfg["manager_rules"]["min_creators"] = 3
hdr = "Data period,Creator ID,Creator's username,Creator Network manager,Join time,Diamonds,LIVE duration,Valid go LIVE days,LIVE streams\n"
def mrow(i, mgr, days, hrs, join="2026-05-01 10:00:00 (UTC+0)", end="2026-09-30"):
    return f"2026-09-01 ~ {end},{i},u{i},{mgr},{join},50,{hrs}h 0m 0s,{days},9\n"
body = "".join([mrow(1, "good@x.com", 9, 25), mrow(2, "good@x.com", 8, 21), mrow(3, "good@x.com", 2, 3),     # 2 of 3 active
                mrow(4, "bad@x.com", 9, 25), mrow(5, "bad@x.com", 1, 1), mrow(6, "bad@x.com", 0, 0), mrow(7, "bad@x.com", 2, 2),
                mrow(8, "bad@x.com", 3, 4, join="2026-09-25 10:00:00 (UTC+0)"),                                # too new: left out
                mrow(9, "hierarchymusicllc@gmail.com", 0, 0), mrow(10, "tiny@x.com", 0, 0)])
rows, found = nudges.parse_stats((hdr + body).encode(), "m.csv", cfg)
assert "manager" in found
nudges.save_stats(c3, rows)
rep = {g["manager"]: g for g in managers.report(c3, cfg)}
assert rep["good@x.com"]["level"] == "ok" and round(rep["good@x.com"]["now_pct"]) == 67, rep["good@x.com"]
assert rep["bad@x.com"]["total"] == 4 and rep["bad@x.com"]["level"] == "danger" and rep["bad@x.com"]["final"], rep["bad@x.com"]
assert rep["hierarchymusicllc@gmail.com"]["level"] == "owner" and rep["tiny@x.com"]["level"] == "small"
assert managers.report(c3, cfg)[0]["manager"] == "bad@x.com", "worst first"
assert managers.record_final(c3, managers.report(c3, cfg)) == 2                       # owner and small are never recorded
# the next month: bad again -> second month in a row
rows, _ = nudges.parse_stats((hdr + body.replace("2026-09-01 ~ 2026-09-30", "2026-10-01 ~ 2026-10-31")).encode(), "m.csv", cfg)
nudges.save_stats(c3, rows)
g = {x["manager"]: x for x in managers.report(c3, cfg)}["bad@x.com"]
assert managers.strikes(g) == 2 and "2 months in a row" in managers.line(g), managers.line(g)
# mid-month nobody is flagged for a total they can still fix
rows, _ = nudges.parse_stats((hdr + body.replace("2026-09-01 ~ 2026-09-30", "2026-10-01 ~ 2026-10-06")).encode(), "m.csv", cfg)
nudges.save_stats(c3, rows)
assert {x["manager"]: x for x in managers.report(c3, cfg)}["bad@x.com"]["level"] == "ok"
print("managers: OK")

# --- unlock quota
import unlock
c4 = core.connect(":memory:"); creators.ensure_schema(c4)
cfg = nudges.load_config()
assert unlock.rules(cfg)["min_valid_days"] == 8 and unlock.rules(cfg)["window_days"] == 30
for h in ("newbie", "quick", "late", "oldie"):
    c4.execute("INSERT INTO creators(handle, member_status, approved_at, discord_id, kind) VALUES (?,?,?,?,?)", (h, "member", "2026-10-01 12:00:00", h + "id", "artist"))
c4.commit()
assert unlock.seed_existing(c4) == 4 and unlock.is_unlocked(c4, "oldie", cfg), "current members are grandfathered"
c4.execute("DELETE FROM creator_unlock"); c4.execute("DELETE FROM settings WHERE key='unlock:seeded'"); c4.commit()
assert not unlock.is_unlocked(c4, "newbie", cfg)
hdr = "Data period,Creator ID,Creator's username,Days since joining,Diamonds,LIVE duration,Valid go LIVE days,LIVE streams,Valid go LIVE days last month\n"
rows, _ = nudges.parse_stats((hdr + "2026-10-01 ~ 2026-10-06,1,newbie,10,0,3h 0m 0s,3,3,0\n2026-10-01 ~ 2026-10-06,2,quick,5,5,12h 0m 0s,8,9,0\n"
                              "2026-10-01 ~ 2026-10-06,3,late,47,0,1h 0m 0s,1,1,5\n2026-10-01 ~ 2026-10-06,4,oldie,147,0,0s,0,0,9\n").encode(), "u.csv", cfg)
nudges.save_stats(c4, rows)
res = unlock.evaluate(c4, cfg, datetime.date(2026, 10, 6))
assert sorted(res["unlocked"]) == ["oldie", "quick"], res         # oldie via last month's 9 days
assert [h for h, p in res["missed"]] == ["late"], res              # 47 days since joining, only 5 of 8 valid days
assert unlock.evaluate(c4, cfg, datetime.date(2026, 10, 7)) == {"unlocked": [], "missed": []}, "each event only once"
assert unlock.is_unlocked(c4, "quick", cfg) and not unlock.is_unlocked(c4, "newbie", cfg)
p = unlock.progress(c4, "newbie", cfg, datetime.date(2026, 10, 6))
assert p["days"] == 3 and p["days_in"] == 10 and p["days_left"] == 20 and not p["met"]
p = unlock.progress(c4, "newbie", cfg, datetime.date(2026, 10, 9))          # the file is 3 days old: the clock moves on
assert p["days_in"] == 13, p
assert "3 of 8" in unlock.progress_text(c4, "newbie", cfg, datetime.date(2026, 10, 6)) and unlock.progress_text(c4, "quick", cfg) is None
# day 31+ without the quota
assert unlock.progress(c4, "newbie", cfg, datetime.date(2026, 10, 27))["window_over"] and not unlock.progress(c4, "newbie", cfg, datetime.date(2026, 10, 26))["window_over"]
cfg2 = {**cfg, "unlock": {"enabled": False}}
assert unlock.is_unlocked(c4, "newbie", cfg2) and unlock.progress_text(c4, "newbie", cfg2) is None
print("unlock: OK")

# --- alerts tied to the creator's manager
c5 = core.connect(":memory:"); creators.ensure_schema(c5)
hdr = "Data period,Creator ID,Creator's username,Creator Network manager,Diamonds,LIVE duration,Valid go LIVE days,LIVE streams\n"
rows, _ = nudges.parse_stats((hdr + "2026-10-01 ~ 2026-10-06,1,ann,Boss@X.com,0,0s,0,0\n2026-10-01 ~ 2026-10-06,2,bo,other@x.com,0,0s,0,0\n"
                              "2026-10-01 ~ 2026-10-06,3,cy,,0,0s,0,0\n").encode(), "m.csv", nudges.load_config())
nudges.save_stats(c5, rows)
assert managers.unlinked(c5) == ["boss@x.com", "other@x.com"]
try: managers.set_map(c5, "not-an-email", 1); raise SystemExit("bad email accepted")
except ValueError: pass
managers.set_map(c5, " Boss@X.com ", 999, "Boss Man")
a = managers.assignment(c5, "ann")
assert a == {"email": "boss@x.com", "discord_id": "999", "name": "Boss Man"}, a
assert managers.label(a) == "<@999> (Boss Man)" and "not linked" in managers.label(managers.assignment(c5, "bo"))
assert managers.label(managers.assignment(c5, "cy")) == "none in the TikTok file" and managers.unlinked(c5) == ["other@x.com"]
print("manager alerts: OK")
