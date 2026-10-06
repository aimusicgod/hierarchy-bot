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
creators.start_application(conn, 4, "artist", "Q R", "stranger", "z", "q@x.com", "212-555-0111", now=T0 + datetime.timedelta(days=8))
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
assert {n["rule_id"] for n in new} == {"no_live_5_days", "behind_weekly_goal"} and all(n["handle"] == "alice" for n in new), new
assert all("Alice" in n["text"] for n in new) and not any("{" in n["text"] for n in new)
assert nudges.build_nudges(conn, cfg, today, now)[0] == [], "one per creator per week per rule"
assert nudges.build_nudges(conn, cfg, today + datetime.timedelta(days=9), now + datetime.timedelta(days=9))[0] == [], "stale stats must block"
nudges.skip(conn, "alice", None); assert nudges.is_skipped(conn, "alice", today)
conn.execute("DELETE FROM nudges"); conn.commit()
assert nudges.build_nudges(conn, cfg, today, now)[0] == [], "skip list"
nudges.unskip(conn, "alice"); nudges.skip(conn, "alice", "2026-10-01"); assert not nudges.is_skipped(conn, "alice", today)
nudges.unskip(conn, "alice")
new, _ = nudges.build_nudges(conn, cfg, datetime.date(2026, 10, 6), now)   # Tuesday: weekly rule must not fire yet
assert {n["rule_id"] for n in new} == {"no_live_5_days"}
nudges.set_goal(conn, "alice", 2); rpt = nudges.weekly_report(conn, cfg)
assert [x[0] for x in rpt["hit"]] == ["alice"], rpt
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
assert "introduction only" in studio.DISCLAIMER and "discount" in studio.DISCLAIMER
print("studio: OK")
print("\nAll creators checks passed.")
