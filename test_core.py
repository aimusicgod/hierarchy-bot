"""Run `python3 test_core.py` to check the money math, imports and automation without Discord."""
import base64, hashlib, hmac, os, tempfile
import core

conn = core.connect(":memory:")

# --- scouting submissions start Pending; duplicates blocked
subs = {}
for n, scout in [("testcreator1", 111), ("testcreator2", 111), ("testcreator3", 222), ("testcreator5", 222), ("testcreator6", 111)]:
    u, url = core.parse_instagram(n, f"instagram.com/{n}")
    subs[n] = core.add_submission(conn, u, url, scout)
assert core.get_submission(conn, subs["testcreator1"])["status"] == "pending"
try:
    core.add_submission(conn, "testcreator1", "x", 999)
    raise SystemExit("duplicate should have been blocked")
except core.DuplicateSubmission:
    pass
print("submissions: OK")

# --- TikTok's creators export sets Effective / Terminated (and it can change either way)
rows = core.parse_creators_export(open("sample_manage_creators.csv", "rb").read(), "sample_manage_creators.csv")
assert [r["handle"] for r in rows] == ["testcreator1", "testcreator2", "testcreator3", "someoneelse", "testcreator5", "tt_mgr_filled"], rows
rep = core.sync_creators(conn, rows)
st = lambda n: core.get_submission(conn, subs[n])["status"]
assert (st("testcreator1"), st("testcreator2"), st("testcreator3"), st("testcreator5")) == \
       ("effective", "effective", "pending", "terminated"), [st(n) for n in subs]
assert rep["unmatched"] == ["someoneelse"] and sorted(rep["registered"]) == ["testcreator1", "testcreator2", "tt_mgr_filled"], rep
assert core.sync_creators(conn, rows)["changed"] == [], "second sync must change nothing"
rows[0]["relationship"] = "Terminated"          # next month TikTok ends creator 1
core.sync_creators(conn, rows)
assert st("testcreator1") == "terminated"
rows[0]["relationship"] = "Effective"           # ...and they come back
core.sync_creators(conn, rows)
assert st("testcreator1") == "effective"
assert st("testcreator6") == "effective", "manager's note 'IG: @testcreator6' should link the TikTok name"
assert core.ig_from_notes("instagram.com/Jane.Doe/") == "jane.doe" and core.ig_from_notes("ig - @a_b") == "a_b"
assert core.ig_from_notes("called her twice") is None and core.ig_from_notes("") is None
print("creators export -> Effective / Terminated, both directions: OK")
print("Instagram name read from TikTok Notes: OK")

# --- link a TikTok name that differs from the Instagram name
core.link_handles(conn, "tt_different", "testcreator2")
rep = core.sync_creators(conn, [{"handle": "tt_different", "relationship": "Effective", "valid_days": 3, "last_live": "x"}])
assert st("testcreator2") == "effective" and rep["unmatched"] == [], rep
core.link_handles(conn, "tt_different", "testcreator2", "Jane@Example.com ", "(305) 555-0147")
row = conn.execute("SELECT email, phone FROM creators WHERE handle='tt_different'").fetchone()
assert (row["email"], row["phone"]) == ("jane@example.com", "3055550147"), tuple(row)
assert core.clean_phone("+1 305 555 0147") == "+13055550147" and core.clean_email("") is None
for bad in (lambda: core.clean_email("nope"), lambda: core.clean_phone("12")):
    try:
        bad(); raise SystemExit("bad contact should be rejected")
    except ValueError:
        pass
print("link TikTok name to Instagram name + email/phone: OK")

# --- earnings: only the scout's 5%
core.upsert_creator(conn, "extra", scout_id=None)
earn = core.parse_export(open("sample_export.csv", "rb").read(), "sample_export.csv")
core.set_submission_status(conn, subs["testcreator2"], "terminated")
r = core.import_rows(conn, earn)
assert r["terminated"] == ["testcreator2"] and r["scout"] == 1000 and r["gross"] == 20000, r  # no 5% once terminated
core.set_submission_status(conn, subs["testcreator2"], "effective")   # ...but if they come back, it is credited
r2 = core.import_rows(conn, earn)
assert r2["imported"] == 1 and r2["scout"] == 500 and r2["terminated"] == [], r2
r = {"scout": r["scout"] + r2["scout"]}
assert core.import_rows(conn, earn)["imported"] == 0, "re-import must not double-pay"
core.add_adjustment(conn, "testcreator1", "202608", -1000, "correction")
assert sum(d["cents"] for d in core.payouts_due(conn, "202608")) == int(r["scout"]) - 50
print("scout 5% + re-import block + adjustment: OK")

# --- W-9 via e-sign portal: link, trace back, payout gate
assert not core.has_w9(conn, "111")
body = b'{"event":"envelope-completed","data":{"envelopeId":"ENV-1","envelopeSummary":{"recipients":{"signers":[{"name":"A Scout","email":"a@x.com","tabs":{"textTabs":[{"tabLabel":"ScoutID","value":"111"}]}}]}}}}'
sig = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
assert core.verify_hmac(body, {"X-DocuSign-Signature-1": sig}, ["secret"])
assert not core.verify_hmac(body, {"X-DocuSign-Signature-1": sig}, ["wrong"])
import json
sid = core.find_scout_id(json.loads(body))
assert sid == "111", sid
assert core.record_w9_event(conn, "ENV-1", "A Scout", "a@x.com", sid) and core.has_w9(conn, "111")
assert conn.execute("SELECT w9_envelope_id FROM payees WHERE payee_id='111'").fetchone()[0] == "ENV-1"
assert core.record_w9_event(conn, "ENV-2", "B Scout", "b@x.com", None) is False and not core.has_w9(conn, "222")
core.assign_w9_event(conn, "ENV-2", 222)
assert core.has_w9(conn, "222")
print("W-9 portal webhook, trace-back, manual assign: OK")

# --- creator onboarding + manager booking (works whichever happens first)
import json as _json, time as _time
c2 = core.connect(":memory:")
u, url = core.parse_instagram("newcreator", "instagram.com/newcreator")
_s = core.add_submission(c2, u, url, 111)
core.set_decision(c2, _s, 'approved')
# booking first: the creator picked a time after your DM, before joining the creator server
core.record_booking(c2, "B1", "New@Creator.com", "New Creator", "mgr1@x.com", "Manager One", "2026-10-07T15:00:00Z")
res = core.onboard_creator(c2, "@NewCreator", "@newtt", "new@creator.com", "(305) 555-0100", 4242)
assert res["handle"] == "newtt" and res["booking"]["manager_name"] == "Manager One"
cr = c2.execute("SELECT * FROM creators WHERE handle='newtt'").fetchone()
assert cr["scout_id"] == "111" and cr["discord_id"] == "4242" and core.payee_name(c2, cr["manager_id"]) == "Manager One"
# reschedule to another manager: manager follows the newest booking
core.record_booking(c2, "B2", "new@creator.com", "New Creator", "mgr2@x.com", "Manager Two", "2026-10-08T15:00:00Z")
assert core.payee_name(c2, c2.execute("SELECT manager_id FROM creators WHERE handle='newtt'").fetchone()[0]) == "Manager Two"
assert c2.execute("SELECT status FROM bookings WHERE uid='B1'").fetchone()[0] == "superseded"
assert core.cancel_booking(c2, "B2")["status"] == "canceled"
# onboarding first, booking later
u3, url3 = core.parse_instagram("second", "instagram.com/second"); core.set_decision(c2, core.add_submission(c2, u3, url3, 222), 'approved')
core.onboard_creator(c2, "second", "secondtt", "second@x.com", "3055550101")
assert c2.execute("SELECT manager_id FROM creators WHERE handle='secondtt'").fetchone()[0] is None
rec = core.record_booking(c2, "B3", "second@x.com", "S", "mgr1@x.com", "Manager One", "t")
assert [h for h, _ in rec] == ["secondtt"]
# bad inputs
for args, exc in [(("nobody", "x", "a@b.com", "3055550101"), core.NotInvited),
                  (("newcreator", "someone_else", "a@b.com", "3055550101"), core.AlreadyClaimed),
                  (("second", "x2", "a@b.com", ""), ValueError)]:
    try:
        core.onboard_creator(c2, *args); raise SystemExit("should have failed: %s" % (args,))
    except exc:
        pass
print("creator onboarding + manager booking, both orders: OK")

# --- scheduling webhooks: signatures + payload parsing
cal = _json.dumps({"event": "invitee.created", "payload": {"uri": "https://api.calendly.com/x/1", "email": "A@b.com", "name": "A",
      "scheduled_event": {"start_time": "2026-10-07T15:00:00Z", "event_memberships": [{"user_email": "m@x.com", "user_name": "M"}]}}}).encode()
ts = str(int(_time.time()))
import hmac as _h, hashlib as _hl
sig = _h.new(b"k", ts.encode() + b"." + cal, _hl.sha256).hexdigest()
assert core.verify_calendly(cal, f"t={ts},v1={sig}", "k") and not core.verify_calendly(cal, f"t={ts},v1={sig}", "bad")
assert not core.verify_calendly(cal, f"t=1,v1={sig}", "k") and not core.verify_calendly(cal, "garbage", "k")
kind, d = core.parse_calendly(_json.loads(cal))
assert kind == "created" and d["manager_email"] == "m@x.com" and d["invitee_email"] == "A@b.com" and d["uid"].endswith("/1")
cc = _json.dumps({"triggerEvent": "BOOKING_CREATED", "payload": {"uid": "u1", "startTime": "t", "organizer": {"name": "O", "email": "o@x.com"},
      "attendees": [{"email": "g@x.com", "name": "G"}]}}).encode()
assert core.verify_calcom(cc, _h.new(b"s", cc, _hl.sha256).hexdigest(), "s") and not core.verify_calcom(cc, "00", "s")
kind, d = core.parse_calcom(_json.loads(cc))
assert kind == "created" and d["manager_email"] == "o@x.com" and d["invitee_email"] == "g@x.com"
assert core.parse_calcom({"triggerEvent": "BOOKING_CANCELLED", "payload": {"uid": "u1"}})[0] == "canceled"
assert core.parse_calendly({"event": "routing_form_submission.created"}) is None
print("scheduling webhooks (Calendly + Cal.com signatures and payloads): OK")

# --- roster + benchmark + backup
assert "111" in core.roster(conn)["scouts"]
with tempfile.TemporaryDirectory() as tmp:
    live = core.connect(os.path.join(tmp, "live.db"))
    core.upsert_creator(live, "testcreator1", scout_id=111)
    core.import_rows(live, earn)
    live.close()
    fresh = core.connect(os.path.join(tmp, "live.db"))
    core.backup_to(fresh, os.path.join(tmp, "b.db"))
    copy = core.connect(os.path.join(tmp, "b.db"))
    assert copy.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] > 0
    copy.close(); fresh.close()
print("roster + backup: OK")
print("\nAll checks passed.")

# --- approve / deny + export for the DM tool
c3 = core.connect(":memory:")
a = core.add_submission(c3, "yes_one", "https://instagram.com/yes_one", 1)
d = core.add_submission(c3, "no_one", "https://instagram.com/no_one", 1)
assert c3.execute("SELECT decision FROM submissions WHERE id=?", (a,)).fetchone()[0] == "review"
core.set_decision(c3, a, "approved"); core.set_decision(c3, d, "denied")
txt, n = core.export_approved(c3)
assert n == 1 and "yes_one" in txt and "no_one" not in txt
assert core.export_approved(c3)[1] == 0 and core.export_approved(c3, only_new=False)[1] == 1
try:
    core.onboard_creator(c3, "no_one", "nott", "n@x.com", "3055550101"); assert 0
except core.NotInvited:
    pass
print("approve/deny + export + onboarding gate: OK")

# --- scout applications
c4 = core.connect(":memory:")
for bad in [("Madonna", "a@b.com", "3055550101"), ("Jane Doe", "nope", "3055550101"), ("Jane Doe", "a@b.com", "")]:
    try:
        core.apply_scout(c4, 7, *bad, True, "v1"); assert 0
    except ValueError:
        pass
try:
    core.apply_scout(c4, 7, "Jane Doe", "jane@x.com", "3055550101"); assert 0      # box not checked
except ValueError:
    pass
s = core.apply_scout(c4, 7, "  Jane   Doe ", "Jane@X.com", "(305) 555-0101", True, "v1")
assert s["agreement_version"] == "v1" and s["agreed_at"]
assert s["full_name"] == "Jane Doe" and s["email"] == "jane@x.com" and s["status"] == "pending"
assert not core.scout_approved(c4, 7)
try:
    core.apply_scout(c4, 7, "Jane Doe", "jane@x.com", "3055550101", True, "v1"); assert 0
except core.AlreadyApplied:
    pass
core.set_scout_status(c4, 7, "denied")
core.apply_scout(c4, 7, "Jane Doe", "jane@x.com", "3055550101", True, "v1")      # can reapply after a denial
core.apply_scout  # (reapplied above without text)
core.set_scout_status(c4, 7, "approved")
assert core.scout_approved(c4, 7)
c4.execute("UPDATE scouts SET agreement_text='FULL TEXT' WHERE discord_id='7'"); c4.commit()
import zipfile, io as _io
zdata, zn = core.export_agreements(c4)
zf = zipfile.ZipFile(_io.BytesIO(zdata))
assert zn == 1 and "FULL TEXT" in zf.read(zf.namelist()[0]).decode() and "Jane Doe" in zf.read(zf.namelist()[0]).decode()
assert c4.execute("SELECT name FROM payees WHERE payee_id='7'").fetchone()[0] == "Jane Doe"
# scouts who already submitted before applications existed stay approved
c5 = core.connect(":memory:"); core.add_submission(c5, "old_one", "https://instagram.com/old_one", 99)
c5.close()
import os, tempfile
p = os.path.join(tempfile.mkdtemp(), "m.db"); c6 = core.connect(p); core.add_submission(c6, "old_one", "https://instagram.com/old_one", 99); c6.close()
assert core.scout_approved(core.connect(p), 99)
print("scout applications: OK")

txt, n = core.export_w9_list(c4)
assert n == 1 and "Jane Doe" in txt and "jane@x.com" in txt
core.set_w9(c4, "7", True)
assert core.export_w9_list(c4)[1] == 0
print("W-9 list export: OK")

# --- payout export (paperwork gate, names, amounts)
c7 = core.connect(":memory:")
core.ensure_payee(c7, "50", "Pat Paperwork"); core.ensure_payee(c7, "51", "Nora NoForm")
for pid, cents in (("50", 5000), ("51", 2500)):
    c7.execute("INSERT INTO ledger(month, handle, role, payee_id, amount_cents, gross_cents, kind, note) "
               "VALUES ('202609','h','scout',?,?,?,'earning','t')", (pid, cents, cents * 20))
c7.commit()
core.set_w9(c7, "50", True)
txt, n, held = core.export_payouts(c7, "202609")
assert n == 1 and "Pat Paperwork,50.00,202609" in txt and "Nora" not in txt and held == ["Nora NoForm"]
print("payout export: OK")

# --- removed scouts: residuals continue unless removed for breach
c8 = core.connect(":memory:")
core.apply_scout(c8, 70, "Rae Removed", "rae@x.com", "3055550170", True, "v")
core.set_scout_status(c8, 70, "approved")
sid = core.add_submission(c8, "rae_artist", "https://instagram.com/rae_artist", 70)
core.set_decision(c8, sid, "approved")
core.link_handles(c8, "raeartist", "rae_artist")
core.set_submission_status(c8, sid, "effective")
row = {"month": "202609", "handle": "raeartist", "gross_cents": 100000}
core.remove_scout(c8, 70, for_breach=False)
assert not core.scout_approved(c8, 70)                       # can't submit new prospects
r1 = core.import_rows(c8, [row])
assert r1["scout"] == 5000 and not r1["forfeited"]            # but residuals continue
core.remove_scout(c8, 70, for_breach=True)
r2 = core.import_rows(c8, [dict(row, month="202610")])
assert r2["scout"] == 0 and r2["forfeited"] == ["raeartist"]  # breach: nothing for later months
assert core.earnings_for(c8, 70)[0]["cents"] == 5000          # earlier month stays
print("removed scouts + breach forfeiture: OK")
