import datetime as dt, os, tempfile
from zoneinfo import ZoneInfo
import core, scheduler as S

class FakeCal:
    def __init__(self, busy): self.b = busy; self.events = {}; self.n = 0
    def busy(self, emails, a, b): return {e: self.b.get(e) for e in emails}
    def create_event(self, summary, start, end, attendees, description=""):
        self.n += 1; self.events[f"ev{self.n}"] = (start, attendees)
        return {"id": f"ev{self.n}", "meet_link": "https://meet.google.com/x"}
    def delete_event(self, i): self.events.pop(i, None)

NY = ZoneInfo("America/New_York")
conn = core.connect(os.path.join(tempfile.mkdtemp(), "t.db"))
S.add_manager(conn, "a@x.com", "Alice"); S.add_manager(conn, "b@x.com", "Bob")
now = dt.datetime(2026, 10, 5, 12, tzinfo=NY)          # Monday
start = S.parse_when("10/07", "2:00pm", "PST", now)     # Wed 2pm PT = 5pm ET
assert start.astimezone(NY).hour == 17, start
cal = FakeCal({"a@x.com": [], "b@x.com": []})
r = S.schedule_call(conn, cal, NY, "kid@x.com", "Kid", start, now)
assert r["manager_name"] == "Alice"
try: S.schedule_call(conn, cal, NY, "kid@x.com", "Kid", start, now); assert 0
except S.AlreadyBooked: pass
s2 = S.parse_when("10/07", "2:00pm", "PST", now)
r = S.schedule_call(conn, cal, NY, "k2@x.com", "K2", s2, now)
assert r["manager_name"] == "Bob"                       # round robin
# Alice busy, Bob unshared -> only Alice-free blocked
s3 = start + dt.timedelta(days=1)
cal2 = FakeCal({"a@x.com": [(s3, s3 + dt.timedelta(hours=1))], "b@x.com": None})
try: S.schedule_call(conn, cal2, NY, "k3@x.com", "K3", s3, now); assert 0
except S.NoSlot as e: assert e.args or True
# outside hours
late = dt.datetime(2026, 10, 7, 21, tzinfo=NY)
try: S.schedule_call(conn, cal, NY, "k4@x.com", "K4", late, now); assert 0
except S.NoSlot: pass
# past / too soon
try: S.schedule_call(conn, cal, NY, "k5@x.com", "K5", now, now); assert 0
except (S.NoSlot, ValueError): pass
S.cancel_call(conn, cal, "kid@x.com"); assert core.latest_booking(conn, "kid@x.com") is None
assert S.remove_manager(conn, "b@x.com")
print("scheduler checks passed")
