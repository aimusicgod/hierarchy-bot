"""Hierarchy Music scout bot (scouting server).
Scouts use buttons; you use an admin panel. Status follows TikTok's creators export (Effective / Terminated).
Scouts only ever see their own 5%. W-9s go through an e-sign portal (DocuSign) and link back here."""
import asyncio
import datetime
import hmac
import json
import os
import tempfile

import discord
from aiohttp import web
from discord import app_commands
from dotenv import load_dotenv

import core
import creators
import creator_server
import scheduler
import welcome
import agreement
from zoneinfo import ZoneInfo

load_dotenv()
TOKEN = os.environ["DISCORD_TOKEN"]
REVIEW_CHANNEL_ID = int(os.environ["REVIEW_CHANNEL_ID"])  # private admin-only channel
GUILD_ID = os.getenv("GUILD_ID")
DB_PATH = os.getenv("DB_PATH", "hierarchy.db")
POWERFORM_URL = os.getenv("DOCUSIGN_POWERFORM_URL", "")   # your DocuSign PowerForm link
HMAC_KEYS = [k.strip() for k in os.getenv("DOCUSIGN_HMAC_KEY", "").split(",") if k.strip()]
PORT = int(os.getenv("PORT", "8080"))
FAQ_CHANNEL_ID = os.getenv("FAQ_CHANNEL_ID")                # read-only FAQs for scouts (auto-posted)
HELP_CHANNEL_ID = os.getenv("HELP_CHANNEL_ID")              # scouts help each other (rules auto-posted)
WELCOME_CHANNEL_ID = os.getenv("WELCOME_CHANNEL_ID")        # public welcome channel (auto-posted)
SCOUT_CHANNEL_ID = os.getenv("SCOUT_CHANNEL_ID")            # scout commands channel (auto-posted)
SCOUT_ROLE_ID = os.getenv("SCOUT_ROLE_ID")                # role that unlocks the scout channels after approval
CREATOR_GUILD_ID = os.getenv("CREATOR_GUILD_ID")          # the separate creator server
CALENDLY_KEY = os.getenv("CALENDLY_SIGNING_KEY", "")
CALCOM_SECRET = os.getenv("CALCOM_SECRET", "")
G_ID = os.getenv("GOOGLE_CLIENT_ID", "")
G_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
G_REFRESH = os.getenv("GOOGLE_REFRESH_TOKEN", "")
ORG_TZ = ZoneInfo(os.getenv("ORG_TIMEZONE", "America/New_York"))   # managers' working hours are in this zone
INTAKE_SECRET = os.getenv("INTAKE_SECRET", "")                    # lets an Instagram DM tool send bookings in
MAX_UPLOAD = 5 * 1024 * 1024
GOLD = 0xD4AF37
BUTTON_STATUSES = ["pending", "effective", "terminated"]
NO_PINGS = discord.AllowedMentions.none()


def db():
    conn = core.connect(DB_PATH)
    creators.ensure_schema(conn)
    return conn


async def say(interaction: discord.Interaction, content=None, **kw):
    """Always reply privately, whether or not we already acknowledged the interaction."""
    kw.setdefault("allowed_mentions", NO_PINGS)
    if interaction.response.is_done():
        return await interaction.followup.send(content, ephemeral=True, **kw)
    return await interaction.response.send_message(content, ephemeral=True, **kw)


def label(conn, payee_id) -> str:
    return f"<@{payee_id}>" if str(payee_id).isdigit() else (core.payee_name(conn, payee_id) or str(payee_id))


async def review_channel():
    return bot.get_channel(REVIEW_CHANNEL_ID) or await bot.fetch_channel(REVIEW_CHANNEL_ID)


async def dm_scout(scout_id, text):
    try:
        user = await bot.fetch_user(int(scout_id))
        await user.send(text)
    except (discord.HTTPException, ValueError):
        pass  # DMs closed


# ---------- review cards ----------
def submission_embed(sub) -> discord.Embed:
    e = discord.Embed(title=f"@{sub['ig_username']}", url=sub["ig_url"], color=GOLD)
    e.add_field(name="Decision", value=core.DECISIONS[sub["decision"]])
    e.add_field(name="TikTok status", value=core.STATUS_LABELS[sub["status"]])
    e.add_field(name="Scout", value=f"<@{sub['scout_id']}>")
    e.set_footer(text=f"Submission #{sub['id']}  |  {sub['created_at']} UTC")
    return e


def review_view(sub_id: int, current: str = "") -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    for d in ("approved", "denied"):
        v.add_item(DecisionButton(sub_id, d, current))
    return v


async def refresh_review_card(sub):
    if not sub["review_msg_id"]:
        return
    try:
        msg = await (await review_channel()).fetch_message(int(sub["review_msg_id"]))
        await msg.edit(embed=submission_embed(sub), view=review_view(sub["id"], sub["decision"]))
    except discord.HTTPException:
        pass


class DecisionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"dec:(?P<id>\d+):(?P<d>approved|denied)"):
    """Approve or deny a scouted prospect. TikTok status (Effective/Terminated) still comes from the creators import."""

    def __init__(self, sub_id: int, decision: str, current: str = ""):
        style = (discord.ButtonStyle.success if decision == "approved" else discord.ButtonStyle.danger) \
            if decision == current else discord.ButtonStyle.secondary
        super().__init__(discord.ui.Button(label="Approve" if decision == "approved" else "Deny", style=style,
                                           custom_id=f"dec:{sub_id}:{decision}"))
        self.sub_id, self.decision = sub_id, decision

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["id"]), match["d"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await say(interaction, "Admins only.")
            return False
        return True

    async def callback(self, interaction: discord.Interaction):
        conn = db()
        sub = core.set_decision(conn, self.sub_id, self.decision)
        if not sub:
            await say(interaction, "This card is out of date: that submission isn't in the database anymore "
                                   "(the database was reset). Delete this message; new submissions will have working buttons.")
            return
        await interaction.response.edit_message(embed=submission_embed(sub), view=review_view(self.sub_id, self.decision))
        word = "approved" if self.decision == "approved" else "not moving forward"
        await dm_scout(sub["scout_id"], f"Update on your submission @{sub['ig_username']}: {word}.")


class StatusButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sub:(?P<id>\d+):(?P<status>[a-z]+)"):
    """Manual override. Status normally follows TikTok when you import the creators file."""

    def __init__(self, sub_id: int, status: str, current: str = ""):
        style = discord.ButtonStyle.success if status == current else discord.ButtonStyle.secondary
        super().__init__(discord.ui.Button(label=core.STATUS_LABELS[status], style=style,
                                           custom_id=f"sub:{sub_id}:{status}"))
        self.sub_id, self.status = sub_id, status

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["id"]), match["status"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            await say(interaction, "Admins only.")
            return False
        return True

    async def callback(self, interaction: discord.Interaction):
        conn = db()
        core.set_submission_status(conn, self.sub_id, self.status)
        sub = core.get_submission(conn, self.sub_id)
        if not sub:
            await say(interaction, "This card is out of date: that submission isn't in the database anymore. Delete this message.")
            return
        await interaction.response.edit_message(embed=submission_embed(sub), view=review_view(self.sub_id, self.status))
        await dm_scout(sub["scout_id"], f"Update on your submission @{sub['ig_username']}: it is now {core.STATUS_LABELS[self.status]}.")
        if self.status == "effective":
            await say(interaction, "Effective. To credit earnings, tap **Onboard prospect** on the admin panel with their "
                                   "TikTok username via **Onboard prospect** (or import the creators file and it links automatically).")


# ---------- the standing posts (kept up to date automatically when the bot starts) ----------
def _page_embed(page, title=None):
    e = discord.Embed(title=title or page["title"], color=GOLD)
    for name, text in page["sections"]:
        e.add_field(name=name, value=text, inline=False)
    return e


def welcome_embed():
    w = welcome.WELCOME
    e = discord.Embed(title=w["title"], description=w["intro"], color=GOLD)
    for name, text in w["sections"]:
        e.add_field(name=name, value=text, inline=False)
    return e


def guide_embeds():
    return [_page_embed(p) for p in welcome.GUIDE]


def faq_embeds():
    out = []
    for i in range(0, len(welcome.FAQ), 8):
        e = discord.Embed(title="Frequently asked questions" if i == 0 else None, color=GOLD)
        for q, a in welcome.FAQ[i:i + 8]:
            e.add_field(name=q, value=a, inline=False)
        out.append(e)
    return out


def help_rules_text():
    return "Scouts helping scouts. Ask questions, share tips, learn from each other.\n\nRules:\n" + \
        "\n".join(f"{i}. {r}" for i, r in enumerate(welcome.HELP_RULES, 1))


def scout_panel_embed():
    return discord.Embed(title="Hierarchy Scouts", color=GOLD,
                         description="Tap a button below. Everything you see is private to you.")


def admin_panel_embed():
    return discord.Embed(title="Admin panel", color=GOLD,
                         description="Import the TikTok creators file to update Effective / Terminated automatically. "
                                     "Import monthly earnings for the 5% payouts.")


def same_embed(msg, embed):
    """True when the posted message already shows exactly this embed, so we can skip editing it
    (Discord rate-limits edits, and every restart used to re-edit every post)."""
    if not msg.embeds:
        return False
    a, b = msg.embeds[0].to_dict(), embed.to_dict()
    keys = ("title", "description", "fields", "color")
    return all(a.get(k) == b.get(k) for k in keys)


async def sync_post(key, channel_id, embed, view=None):
    """Post once; on later starts edit that same message so the text is always current."""
    if not channel_id:
        return
    conn = db()
    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        mid = core.get_setting(conn, f"post:{key}")
        if mid:
            try:
                msg = await channel.fetch_message(int(mid))
                if not same_embed(msg, embed):
                    await msg.edit(embed=embed, view=view)
                elif view is not None:
                    await msg.edit(view=view)   # re-attach buttons; cheap and keeps them working
                return
            except discord.NotFound:
                pass
        msg = await channel.send(embed=embed, view=view)
        core.set_setting(conn, f"post:{key}", msg.id)
    except (discord.HTTPException, ValueError) as e:
        print(f"Couldn't update the '{key}' post (check the bot's permissions in that channel): {e}")
    finally:
        conn.close()


async def sync_help():
    """Forum channels hold their rules in the channel topic; ordinary channels get a posted message."""
    if not HELP_CHANNEL_ID:
        return
    try:
        channel = bot.get_channel(int(HELP_CHANNEL_ID)) or await bot.fetch_channel(int(HELP_CHANNEL_ID))
        if isinstance(channel, discord.ForumChannel):
            await channel.edit(topic=help_rules_text()[:4096])
            return
    except (discord.HTTPException, ValueError) as e:
        print(f"Couldn't update the help channel rules (check the bot's permissions there): {e}")
        return
    await sync_post("help", HELP_CHANNEL_ID, discord.Embed(title="Help: scouts helping scouts", color=GOLD,
                                                           description=help_rules_text()))


async def ensure_posts():
    await sync_post("welcome", WELCOME_CHANNEL_ID, welcome_embed(), ApplyPanel())
    for i, e in enumerate(faq_embeds()):
        await sync_post(f"faq{i}", FAQ_CHANNEL_ID, e)
    await sync_help()
    await sync_scout_channel()
    await sync_post("admin", REVIEW_CHANNEL_ID, admin_panel_embed(), AdminPanel())


async def sync_scout_channel():
    """Keep scout-commands clean: the instructions first, the buttons last.
    If anything else is in the channel (old posts, a panel that ended up above newer messages),
    the bot clears its own messages there and posts everything again in the right order."""
    if not SCOUT_CHANNEL_ID:
        return
    conn = db()
    try:
        channel = bot.get_channel(int(SCOUT_CHANNEL_ID)) or await bot.fetch_channel(int(SCOUT_CHANNEL_ID))
        embeds = guide_embeds()
        want = [f"guide{i}" for i in range(len(embeds))] + ["scout"]
        ids = [core.get_setting(conn, f"post:{k}") for k in want]
        in_order = False
        if all(ids):
            recent = [m async for m in channel.history(limit=len(want) + 1)]  # newest first
            in_order = [m.id for m in reversed(recent)] == [int(i) for i in ids]
        if in_order:
            for i, e in enumerate(embeds):
                msg = await channel.fetch_message(int(ids[i]))
                if not same_embed(msg, e):
                    await msg.edit(embed=e)
            msg = await channel.fetch_message(int(ids[-1]))
            if not same_embed(msg, scout_panel_embed()):
                await msg.edit(embed=scout_panel_embed(), view=ScoutPanel())
            return
        async for m in channel.history(limit=200):
            if m.author.id == bot.user.id:
                try:
                    await m.delete()
                except discord.HTTPException:
                    pass
        for i, e in enumerate(embeds):
            msg = await channel.send(embed=e)
            core.set_setting(conn, f"post:guide{i}", msg.id)
        msg = await channel.send(embed=scout_panel_embed(), view=ScoutPanel())
        core.set_setting(conn, "post:scout", msg.id)
    except (discord.HTTPException, ValueError) as e:
        print(f"Couldn't update scout-commands (the bot needs View Channel, Send Messages, Embed Links "
              f"and Read Message History there): {e}")
    finally:
        conn.close()


# ---------- bot + web hook ----------
class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        if os.getenv("MEMBERS_INTENT") == "1":   # also switch on 'Server Members Intent' in the Discord developer portal
            intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def on_member_join(self, member):
        try:
            await creator_server.on_member_join(member)
        except Exception as e:
            print(f"[creators] on_member_join failed: {e}")

    async def on_ready(self):
        print("[startup] bot is in these servers: " + ", ".join(f"{g.name} ({g.id})" for g in self.guilds), flush=True)
        for label, gid in (("GUILD_ID (scouts/staff)", GUILD_ID), ("CREATOR_GUILD_ID (creators)", CREATOR_GUILD_ID)):
            if gid and not any(str(g.id) == str(gid) for g in self.guilds):
                print(f"[startup] WARNING: {label} = {gid}, but the bot is NOT in a server with that ID.", flush=True)
        if not getattr(self, "_posts_done", False):
            self._posts_done = True
            await ensure_posts()

    async def setup_hook(self):
        self.add_dynamic_items(StatusButton, DecisionButton, ScoutDecisionButton)
        self.add_view(ApplyPanel())
        self.add_view(ScoutPanel())
        self.add_view(AdminPanel())
        creator_server.init(bot=self, db=db, say=say, review_channel=review_channel, file_modal=FileModal,
                            org_tz=ORG_TZ, guild_id=CREATOR_GUILD_ID, staff_guild_id=GUILD_ID,
                            nudge_channel_id=os.getenv("NUDGE_CHANNEL_ID"), gold=GOLD, no_pings=NO_PINGS, max_upload=MAX_UPLOAD)
        creator_server.register(self)
        print(f"[startup] Hierarchy bot with creators server loaded (creators server {'ON' if CREATOR_GUILD_ID else 'OFF'})", flush=True)
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"[startup] staff server {GUILD_ID}: registered {len(synced)} commands", flush=True)
        else:
            await self.tree.sync()
        if CREATOR_GUILD_ID:
            try:
                synced = await self.tree.sync(guild=discord.Object(id=int(CREATOR_GUILD_ID)))
                print(f"[startup] creators server {CREATOR_GUILD_ID}: registered {sorted(c.name for c in synced)}", flush=True)
            except (discord.Forbidden, discord.HTTPException, ValueError) as e:
                print(f"[creators] Couldn't register the creators-server commands ({e}). Is the bot invited to that server "
                      "(with the 'applications.commands' scope) and is CREATOR_GUILD_ID the right server ID?", flush=True)
        creator_server.start_loops(self)
        if HMAC_KEYS or CALENDLY_KEY or CALCOM_SECRET or INTAKE_SECRET or creator_server.needs_web_server():  # webhooks arrive here from DocuSign / your scheduler
            app = web.Application()
            app.router.add_post("/docusign", docusign_hook)
            app.router.add_post("/calendly", calendly_hook)
            app.router.add_post("/calcom", calcom_hook)
            app.router.add_post("/intake", intake_hook)
            for _m, _path, _fn in creator_server.web_routes():
                app.router.add_post(_path, _fn)
            app.router.add_get("/health", lambda r: web.Response(text="ok"))
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", PORT).start()


bot = Bot()
admin_only = [app_commands.default_permissions(administrator=True),
              app_commands.checks.has_permissions(administrator=True), app_commands.guild_only()]


def apply(decorators):
    def wrap(fn):
        for d in reversed(decorators):
            fn = d(fn)
        return fn
    return wrap


@bot.tree.error
async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await say(interaction, "Admins only." if isinstance(error, app_commands.MissingPermissions)
              else f"Something went wrong: {error}")


async def docusign_hook(request: web.Request):
    body = await request.read()
    if not core.verify_hmac(body, dict(request.headers), HMAC_KEYS):
        return web.Response(status=401, text="bad signature")
    try:
        payload = json.loads(body)
    except ValueError:
        return web.Response(status=400)
    data = payload.get("data") or {}
    summary = data.get("envelopeSummary") or {}
    if payload.get("event") != "envelope-completed" and summary.get("status") != "completed":
        return web.Response(text="ignored")
    envelope = data.get("envelopeId") or payload.get("envelopeId")
    if not envelope:
        return web.Response(status=400)
    signers = ((summary.get("recipients") or {}).get("signers")) or [{}]
    name, email = signers[0].get("name"), signers[0].get("email")
    scout_id = core.find_scout_id(payload)
    conn = db()
    linked = core.record_w9_event(conn, envelope, name, email, scout_id)
    channel = await review_channel()
    if linked:
        core.ensure_payee(conn, scout_id)
        await channel.send(f"W-9 received from <@{scout_id}> (DocuSign envelope `{envelope}`). Marked on file.",
                           allowed_mentions=NO_PINGS)
        await dm_scout(scout_id, "Your W-9 was received. Thank you!")
    else:
        await channel.send(f"W-9 completed by **{name or 'unknown'}** ({email or 'no email'}), envelope `{envelope}`, "
                           f"but I couldn't tell which scout it is. Run `/w9assign envelope:{envelope} scout:@scout`.",
                           allowed_mentions=NO_PINGS)
    return web.Response(text="ok")


async def process_booking(kind, d):
    conn = db()
    when = ""
    if d.get("start_time"):
        try:
            ts = int(datetime.datetime.fromisoformat(d["start_time"].replace("Z", "+00:00")).timestamp())
            when = f" for <t:{ts}:f>"
        except ValueError:
            pass
    channel = await review_channel()
    who = f"**{d.get('invitee_name') or 'a creator'}** ({d.get('invitee_email')})"
    mgr = d.get("manager_name") or d.get("manager_email") or "no host"
    if kind == "created":
        linked = core.record_booking(conn, d["uid"], d["invitee_email"], d["invitee_name"],
                                     d["manager_email"], d["manager_name"], d["start_time"])
        tail = (f" Linked to creator @{linked[0][0]}." if linked else
                " No creator with this email yet; it will link when they finish onboarding with the same email.")
        await channel.send(f"Intro call booked: {who} with **{mgr}**{when}.{tail}", allowed_mentions=NO_PINGS)
    else:
        row = core.cancel_booking(conn, d["uid"])
        if row:
            await channel.send(f"Intro call canceled: {who}. Their manager assignment is unchanged until they rebook.",
                               allowed_mentions=NO_PINGS)


async def _booking_hook(request, parser, verified):
    body = await request.read()
    if not verified(body, request.headers):
        return web.Response(status=401, text="bad signature")
    try:
        parsed = parser(json.loads(body))
    except ValueError:
        return web.Response(status=400)
    if parsed is None:
        return web.Response(text="ignored")
    kind, d = parsed
    if not d.get("uid") or not d.get("invitee_email"):
        return web.Response(status=400, text="missing booking details")
    await process_booking(kind, d)
    return web.Response(text="ok")


async def calendly_hook(request: web.Request):
    return await _booking_hook(request, core.parse_calendly, lambda body, h: bool(CALENDLY_KEY) and
                               core.verify_calendly(body, h.get("Calendly-Webhook-Signature", ""), CALENDLY_KEY))


async def calcom_hook(request: web.Request):
    return await _booking_hook(request, core.parse_calcom, lambda body, h: bool(CALCOM_SECRET) and
                               core.verify_calcom(body, h.get("x-cal-signature-256", ""), CALCOM_SECRET))



# ---------- intro-call scheduling (creator's email + time + time zone -> free manager gets the invite) ----------
def _book_sync(email, name, date, time, tz, start_iso, ig):
    """Runs in a worker thread with its own DB connection. Returns (ok, message, result)."""
    conn = db()
    try:
        if not (G_ID and G_SECRET and G_REFRESH):
            raise scheduler.NotConnected()
        cal = scheduler.GoogleCalendar(G_ID, G_SECRET, G_REFRESH)
        start = scheduler.parse_iso(start_iso, tz) if start_iso else scheduler.parse_when(date, time, tz)
        r = scheduler.schedule_call(conn, cal, ORG_TZ, email, name, start, ig_username=ig)
        return True, f"Invite sent to {email}. Call with {r['manager_name']}.", r
    except scheduler.NotConnected:
        return False, "Google Calendar isn't connected yet (see README: Google Calendar setup).", None
    except scheduler.AlreadyBooked as e:
        return False, "That email already has an intro call booked. Cancel it first to rebook.", e.booking
    except scheduler.NoSlot as e:
        opts = ", ".join(t.astimezone(ORG_TZ).strftime("%a %b %d %I:%M%p").replace(" 0", " ") + " ET"
                         if ORG_TZ.key == "America/New_York" else t.astimezone(ORG_TZ).strftime("%a %b %d %H:%M")
                         for t in e.suggestions)
        return False, "No manager is free then." + (f" Next openings: {opts}." if opts else " No openings in the next week."), None
    except ValueError as e:
        return False, str(e), None
    except Exception as e:  # Google/network problems
        return False, f"Couldn't book: {e}", None
    finally:
        conn.close()


async def book_call(email, name="", date="", time="", tz="", start_iso="", ig=None):
    ok, msg, r = await asyncio.to_thread(_book_sync, email, name, date, time, tz, start_iso, ig)
    if ok:
        ts = int(r["start"].timestamp())
        try:
            channel = await review_channel()
            await channel.send(f"Intro call booked: **{name or email}** ({email}) with **{r['manager_name']}** "
                               f"<t:{ts}:f>. Invite sent automatically.", allowed_mentions=NO_PINGS)
        except Exception:
            pass
    return ok, msg


async def intake_hook(request: web.Request):
    """POST JSON {email, name?, date, time, timezone, instagram?} (or {email, start: ISO8601, timezone?}).
    Header X-Intake-Secret must match INTAKE_SECRET. Reply is JSON a DM tool can read back to the creator."""
    if not INTAKE_SECRET or not hmac.compare_digest(request.headers.get("X-Intake-Secret", ""), INTAKE_SECRET):
        return web.Response(status=401, text="bad secret")
    try:
        d = await request.json()
    except ValueError:
        return web.Response(status=400, text="bad json")
    g = lambda k: str(d.get(k) or "").strip()
    ok, msg = await book_call(g("email"), g("name"), g("date"), g("time"), g("timezone") or g("tz"),
                              g("start"), g("instagram") or None)
    return web.json_response({"ok": ok, "message": msg}, status=200 if ok else 422)


class ScheduleModal(discord.ui.Modal, title="Schedule intro call"):
    email = discord.ui.TextInput(label="Creator's email", max_length=120)
    when_date = discord.ui.TextInput(label="Date", placeholder="10/8 or Thursday", max_length=30)
    when_time = discord.ui.TextInput(label="Time", placeholder="3:30pm", max_length=20)
    tz = discord.ui.TextInput(label="Creator's time zone", placeholder="EST, PST, CST...", max_length=40)
    name = discord.ui.TextInput(label="Name (optional)", required=False, max_length=80)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, msg = await book_call(self.email.value, self.name.value, self.when_date.value,
                                  self.when_time.value, self.tz.value)
        await interaction.followup.send(msg, ephemeral=True)

# ---------- scout applications (full name, email, phone before they get in) ----------
def scout_embed(s) -> discord.Embed:
    e = discord.Embed(title=f"Scout application: {s['full_name']}", color=GOLD)
    e.add_field(name="Discord", value=f"<@{s['discord_id']}>")
    e.add_field(name="Status", value=s["status"].title())
    e.add_field(name="Email", value=s["email"], inline=False)
    e.add_field(name="Phone", value=s["phone"])
    if s["agreed_at"]:
        e.add_field(name="Agreement", value=f"Accepted v{s['agreement_version']} on {s['agreed_at']} UTC", inline=False)
    return e


def scout_view(discord_id, current=""):
    v = discord.ui.View(timeout=None)
    for d in ("approved", "denied"):
        v.add_item(ScoutDecisionButton(discord_id, d, current))
    return v


class ScoutDecisionButton(discord.ui.DynamicItem[discord.ui.Button],
                          template=r"scoutdec:(?P<id>\d+):(?P<d>approved|denied)"):
    def __init__(self, discord_id, decision, current=""):
        style = (discord.ButtonStyle.success if decision == "approved" else discord.ButtonStyle.danger) \
            if decision == current else discord.ButtonStyle.secondary
        super().__init__(discord.ui.Button(label="Approve scout" if decision == "approved" else "Deny",
                                           style=style, custom_id=f"scoutdec:{discord_id}:{decision}"))
        self.discord_id, self.decision = str(discord_id), decision

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["id"], match["d"])

    async def interaction_check(self, interaction):
        if not interaction.user.guild_permissions.administrator:
            await say(interaction, "Admins only.")
            return False
        return True

    async def callback(self, interaction):
        conn = db()
        s = core.set_scout_status(conn, self.discord_id, self.decision)
        await interaction.response.edit_message(embed=scout_embed(s), view=scout_view(self.discord_id, self.decision))
        granted = False
        if self.decision == "approved":
            if SCOUT_ROLE_ID and interaction.guild:
                try:
                    member = await interaction.guild.fetch_member(int(self.discord_id))
                    await member.add_roles(discord.Object(id=int(SCOUT_ROLE_ID)))
                    granted = True
                except (discord.HTTPException, ValueError):
                    pass
            await dm_scout(self.discord_id, "You're approved as a Hierarchy scout! Head to the scout channel and tap "
                                            "**Submit a prospect** to get started. We'll email you a W-9 to sign.")
            await interaction.followup.send(
                f"Approved {s['full_name']}." + ("" if granted or not SCOUT_ROLE_ID else " I couldn't give them the role; check my permissions.")
                + f" Next: send their W-9 + Direct Deposit packet in DocuSign to **{s['email']}**, then run `/w9` for <@{self.discord_id}> when both are signed.",
                ephemeral=True, allowed_mentions=NO_PINGS)
        else:
            await dm_scout(self.discord_id, "Thanks for applying. We aren't able to bring you on as a scout right now.")


async def act_apply(interaction, full_name, email, phone, agreed):
    conn = db()
    try:
        s = core.apply_scout(conn, interaction.user.id, full_name, email, phone, agreed, agreement.VERSION,
                              agreement.full_text())
    except core.AlreadyApplied as a:
        return await say(interaction, "You're already approved." if a.status == "approved" else
                         "Your application is already in. You'll get a DM when it's reviewed.")
    except ValueError as e:
        return await say(interaction, str(e))
    msg = await (await review_channel()).send(embed=scout_embed(s), view=scout_view(s["discord_id"], ""),
                                              allowed_mentions=NO_PINGS)
    conn.execute("UPDATE scouts SET review_msg_id=? WHERE discord_id=?", (str(msg.id), s["discord_id"]))
    conn.commit()
    await say(interaction, "Application sent. You'll get a DM when you're approved.")


class ApplyModal(discord.ui.Modal, title="Apply to be a scout"):
    full_name = discord.ui.TextInput(label="Full name", max_length=80)
    email = discord.ui.TextInput(label="Email", max_length=120)
    phone = discord.ui.TextInput(label="Phone number", max_length=30)
    agree = discord.ui.Label(text="I have read and agree to the Scout Agreement",
                             description="Checking this and submitting is my electronic signature.",
                             component=discord.ui.Checkbox())

    async def on_submit(self, interaction):
        await act_apply(interaction, self.full_name.value, self.email.value, self.phone.value,
                        bool(self.agree.component.value))


def agreement_embeds():
    half = len(agreement.SECTIONS) // 2
    out = []
    for i, part in enumerate((agreement.SECTIONS[:half], agreement.SECTIONS[half:])):
        e = discord.Embed(title=agreement.TITLE if i == 0 else None, color=GOLD,
                          description="Please read this before you apply." if i == 0 else None)
        for name, text in part:
            e.add_field(name=name, value=text, inline=False)
        if i == 1:
            e.set_footer(text=f"Version {agreement.VERSION}")
        out.append(e)
    return out


class ContinueView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=900)

    @discord.ui.button(label="I've read it. Continue", style=discord.ButtonStyle.success)
    async def go(self, interaction, button):
        await interaction.response.send_modal(ApplyModal())


class ApplyPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply to be a scout", style=discord.ButtonStyle.primary, custom_id="panel:apply")
    async def apply_btn(self, interaction, button):
        await interaction.response.send_message(embeds=agreement_embeds(), view=ContinueView(), ephemeral=True)


# ---------- actions (shared by buttons and slash commands) ----------
async def act_submit(interaction, username, link):
    if not core.scout_approved(db(), interaction.user.id):
        return await say(interaction, "You need to be approved as a scout first. Use **Apply to be a scout** in the welcome channel.")
    try:
        u, url = core.parse_instagram(username, link)
    except ValueError as e:
        return await say(interaction, str(e))
    conn = db()
    try:
        sub_id = core.add_submission(conn, u, url, interaction.user.id)
    except core.DuplicateSubmission as d:
        mine = d.row["scout_id"] == str(interaction.user.id)
        return await say(interaction, f"You already submitted @{u}." if mine else
                         f"@{u} was already submitted by another scout (first submission gets credit).")
    core.ensure_payee(conn, interaction.user.id, interaction.user.display_name)
    msg = await (await review_channel()).send(embed=submission_embed(core.get_submission(conn, sub_id)),
                                              view=review_view(sub_id, "review"), allowed_mentions=NO_PINGS)
    core.set_review_msg(conn, sub_id, msg.id)
    await say(interaction, f"Got it. @{u} is submission #{sub_id} and is waiting for review. "
                           "You'll get a DM when it's approved or denied, and again when Hierarchy Music confirms them as Effective.")


def scout_status_label(x):
    """What a scout sees for one submission. Approval comes first; after that Hierarchy Music's confirmed status."""
    if x["decision"] == "denied":
        return "Not moving forward"
    if x["decision"] != "approved":
        return "In review"
    if x["status"] == "pending":
        return "Approved, awaiting confirmation"
    return core.STATUS_LABELS[x["status"]]


async def act_mystatus(interaction):
    subs = core.submissions_for(db(), interaction.user.id)
    if not subs:
        return await say(interaction, "No submissions yet. Tap **Submit a prospect**.")
    counts = {}
    for x in subs:
        k = scout_status_label(x)
        counts[k] = counts.get(k, 0) + 1
    e = discord.Embed(title="Your submissions", color=GOLD,
                      description="\n".join(f"@{x['ig_username']}: {scout_status_label(x)}" for x in subs[:25]))
    e.set_footer(text=f"Total {len(subs)}  |  " + "  ".join(f"{k}: {v}" for k, v in counts.items()))
    await say(interaction, embed=e)


async def act_myearnings(interaction):
    conn = db()
    rows = core.earnings_for(conn, interaction.user.id)
    if not rows:
        return await say(interaction, "No earnings recorded yet.")
    by_month, total, unpaid = {}, 0, 0
    for r in rows:
        by_month.setdefault(r["month"], []).append(r)
        total += r["cents"]
        unpaid += 0 if r["all_paid"] else r["cents"]
    e = discord.Embed(title="Your earnings (5%)", color=GOLD)
    for mo, items in list(by_month.items())[:6]:
        body = "\n".join(f"@{i['handle']}: {core.money(i['cents'])}{'' if i['all_paid'] else ' ⏳'}" for i in items)
        e.add_field(name=f"{mo[:4]}-{mo[4:]}  ·  {core.money(sum(i['cents'] for i in items))}", value=body[:1000], inline=False)
    foot = f"Total {core.money(total)}  |  Unpaid {core.money(unpaid)}  (⏳ = unpaid)"
    if unpaid and not core.has_w9(conn, interaction.user.id):
        foot += "\nYour W-9 isn't on file yet. Tap **W-9 / tax form** before payout."
    e.set_footer(text=foot)
    await say(interaction, embed=e)


async def act_w9(interaction):
    conn = db()
    if core.has_w9(conn, interaction.user.id):
        return await say(interaction, "Your W-9 is on file. ✅")
    s = core.get_scout(conn, interaction.user.id)
    email = s["email"] if s else None
    key = f"w9req:{interaction.user.id}"
    last = core.get_setting(conn, key)
    recent = False
    if last:
        try:
            recent = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(last)).total_seconds() < 86400
        except ValueError:
            pass
    if not recent:
        core.set_setting(conn, key, datetime.datetime.now(datetime.timezone.utc).isoformat())
        try:
            await (await review_channel()).send(
                f"**W-9 + Direct Deposit packet requested** by {s['full_name'] if s else interaction.user.display_name} (<@{interaction.user.id}>). "
                f"Send the DocuSign packet to **{email or 'their email (not on file)'}**, then run `/w9` for them when both are signed.",
                allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            pass
    await say(interaction, "Your request has been sent to Hierarchy Music. "
                           f"We'll send your W-9 and Direct Deposit form from DocuSign to {email or 'your email'}, so keep an eye on your inbox and spam folder. "
                           "It isn't instant, since we send it by hand. Once you've signed both, this will show as on file.")


async def act_import_earnings(interaction, att: discord.Attachment):
    if att.size > MAX_UPLOAD:
        return await say(interaction, "File is too large.")
    try:
        r = core.import_rows(db(), core.parse_export(await att.read(), att.filename))
    except ValueError as e:
        return await say(interaction, str(e))
    e = discord.Embed(title=f"Earnings import: {', '.join(sorted(r['months'])) or 'no data'}", color=GOLD)
    e.add_field(name="Creators credited", value=str(r["imported"]))
    e.add_field(name="Total bonus", value=core.money(r["gross"]))
    e.add_field(name="Scout 5% total", value=core.money(r["scout"]))
    e.add_field(name="Already imported", value=str(r["already"]))
    if r["terminated"]:
        e.add_field(name=f"Terminated, no 5% ({len(r['terminated'])})",
                    value=", ".join(f"@{h}" for h in r["terminated"][:30])[:1000], inline=False)
    if r["forfeited"]:
        e.add_field(name=f"Scout removed for breach, no 5% ({len(r['forfeited'])})",
                    value=", ".join(f"@{h}" for h in r["forfeited"][:30])[:1000], inline=False)
    if r["no_scout"]:
        e.add_field(name="No scout linked (not credited)", value=", ".join(f"@{h}" for h in r["no_scout"][:30]), inline=False)
    if r["unmatched"]:
        e.add_field(name=f"Not in the scouting list ({len(r['unmatched'])}, skipped)",
                    value=", ".join(f"@{h}" for h in r["unmatched"][:30])[:1000], inline=False)
    await say(interaction, embed=e)


async def act_import_creators(interaction, att: discord.Attachment):
    if att.size > MAX_UPLOAD:
        return await say(interaction, "File is too large.")
    conn = db()
    try:
        rep = core.sync_creators(conn, core.parse_creators_export(await att.read(), att.filename))
    except ValueError as e:
        return await say(interaction, str(e))
    lines = []
    for sub_id, ig, scout_id, old, new in rep["changed"]:
        lines.append(f"@{ig}: {core.STATUS_LABELS[old]} → **{core.STATUS_LABELS[new]}**")
        sub = core.get_submission(conn, sub_id)
        await refresh_review_card(sub)
        await dm_scout(scout_id, f"Update on your submission @{ig}: it is now: {core.STATUS_LABELS[new]}.")
    for handle, old_ts, new_ts in rep.get("managed_changed", []):
        try:
            await creator_server.on_status_change(handle, old_ts, new_ts)
        except Exception as ex:
            print(f"[creators] status change failed for {handle}: {ex}")
    e = discord.Embed(title="Creators import", color=GOLD,
                      description="\n".join(lines)[:3500] or "No status changes.")
    e.add_field(name="Updated", value=str(len(rep["changed"])))
    e.add_field(name="Unchanged", value=str(rep["same"]))
    e.add_field(name="Newly linked for earnings", value=str(len(rep["registered"])))
    if rep["unmatched"]:
        e.add_field(name=f"Not matched to a submission ({len(rep['unmatched'])})",
                    value=(", ".join(f"@{h}" for h in rep["unmatched"][:30]) +
                           "\nTap **Onboard prospect** to link them, or put their Instagram name in TikTok Notes.")[:1000], inline=False)
    await say(interaction, embed=e)


async def act_link(interaction, tiktok, ig, email=None, phone=None):
    try:
        sub = core.link_handles(db(), tiktok, ig, email, phone)
    except ValueError as e:
        return await say(interaction, str(e))
    saved = ", ".join(x for x, v in (("email", email), ("phone", phone)) if v and v.strip()) or "no contact details"
    await say(interaction, f"Linked TikTok @{core.norm_handle(tiktok)} to Instagram @{sub['ig_username']} "
                           f"(scout <@{sub['scout_id']}>). Saved: {saved}. "
                           "Their next creators import will update the status.")


async def act_roster(interaction, view):
    conn = db()
    r = core.roster(conn)
    lines = []
    if view == "scouts":
        counts = core.submission_counts(conn)
        for sid, handles in sorted(r["scouts"].items(), key=lambda kv: -len(kv[1])):
            s = core.get_scout(conn, sid)
            who = f" {s['full_name']} ({s['email']})" if s and s["full_name"] else ""
            lines.append(f"<@{sid}>{who}: {counts.get(sid, 0)} submitted, {len(handles)} linked"
                         f"{'' if core.has_w9(conn, sid) else '  ⚠️ no W-9'}")
    else:
        for c in r["creators"]:
            contact = " · ".join(x for x in (c["email"], c["phone"]) if x)
            lines.append(f"@{c['handle']}: scout {label(conn, c['scout_id']) if c['scout_id'] else '-'}"
                         f" · manager {label(conn, c['manager_id']) if c['manager_id'] else '-'}"
                         f"{'  |  ' + contact if contact else ''}")
    e = discord.Embed(title=f"{view.title()} ({len(lines)})", description="\n".join(lines)[:4000] or "Nothing here yet.", color=GOLD)
    await say(interaction, embed=e)


class PayView(discord.ui.View):
    def __init__(self, people, month):
        super().__init__(timeout=900)
        for payee_id, name, w9_ok in people[:20]:
            btn = discord.ui.Button(label=(f"Paid: {name}" if w9_ok else f"W-9 needed: {name}")[:80],
                                    style=discord.ButtonStyle.success if w9_ok else discord.ButtonStyle.secondary,
                                    disabled=not w9_ok)

            async def callback(interaction, payee_id=payee_id, btn=btn, name=name):
                n = core.mark_paid(db(), payee_id, month)
                btn.disabled, btn.label = True, f"Paid ✓ {name}"[:80]
                await interaction.response.edit_message(view=self)
                await interaction.followup.send(f"Marked {n} ledger lines paid for {name}.", ephemeral=True)

            btn.callback = callback
            self.add_item(btn)


async def act_payouts(interaction, month=None):
    conn = db()
    try:
        due = core.payouts_due(conn, month or None)
    except ValueError as e:
        return await say(interaction, str(e))
    people, lines = [], []
    for d in due:
        pid = d["payee_id"]
        member = interaction.guild.get_member(int(pid)) if interaction.guild and pid.isdigit() else None
        ok = core.has_w9(conn, pid)
        people.append((pid, member.display_name if member else (core.payee_name(conn, pid) or pid), ok))
        lines.append(f"{label(conn, pid)}: **{core.money(d['cents'])}**{'' if ok else '  ⚠️ no W-9'}")
    e = discord.Embed(title=f"Scout payouts owed {month or '(all unpaid)'}", description="\n".join(lines) or "Nothing owed.", color=GOLD)
    await say(interaction, embed=e, view=PayView(people, month or None) if people else discord.utils.MISSING)


async def act_export_approved(interaction, everyone=False):
    text, n = core.export_approved(db(), only_new=not everyone)
    if not n:
        return await say(interaction, "No newly approved prospects since the last export.")
    path = os.path.join(tempfile.mkdtemp(), "approved_prospects.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(text)
    await say(interaction, f"{n} approved prospect(s). Upload this to your Instagram DM tool.",
              file=discord.File(path, filename="approved_prospects.csv"))


async def act_export_agreements(interaction):
    data, n = core.export_agreements(db())
    if not n:
        return await say(interaction, "No accepted agreements yet.")
    path = os.path.join(tempfile.mkdtemp(), "scout_agreements.zip")
    with open(path, "wb") as f:
        f.write(data)
    await say(interaction, f"{n} signed agreement record(s). One file per scout, with the full text they accepted.",
              file=discord.File(path, filename="scout_agreements.zip"))


async def act_export_w9(interaction):
    text, n = core.export_w9_list(db())
    if not n:
        return await say(interaction, "Every approved scout already has a W-9 on file.")
    path = os.path.join(tempfile.mkdtemp(), "w9_needed.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(text)
    await say(interaction, f"{n} approved scout(s) still need a W-9. Upload this to DocuSign Bulk Send, "
                           "then run `/w9` for each one when they sign.", file=discord.File(path, filename="w9_needed.csv"))


async def act_export_payouts(interaction, month=None):
    try:
        text, n, held = core.export_payouts(db(), month or None)
    except ValueError as e:
        return await say(interaction, str(e))
    if not n:
        extra = f" Held back for missing paperwork: {', '.join(held)}." if held else ""
        return await say(interaction, "Nobody is ready to be paid." + extra)
    path = os.path.join(tempfile.mkdtemp(), "payouts.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(text)
    extra = f"\nHeld back (no W-9 / Direct Deposit on file): {', '.join(held)}." if held else ""
    await say(interaction, f"{n} scout(s) ready to pay. Name and amount only. Bank details stay in DocuSign.{extra}",
              file=discord.File(path, filename="payouts.csv"))


async def act_backup(interaction):
    await interaction.response.defer(ephemeral=True)
    name = f"hierarchy-backup-{datetime.date.today().isoformat()}.db"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, name)
        core.backup_to(db(), path)
        if os.path.getsize(path) > 24 * 1024 * 1024:
            return await say(interaction, "The database is too large to send through Discord.")
        await say(interaction, "Backup attached. Keep it somewhere private.", file=discord.File(path, filename=name))


# ---------- pop-up forms ----------
class SubmitModal(discord.ui.Modal, title="Submit a prospect"):
    username = discord.ui.TextInput(label="Instagram username", placeholder="janedoe", max_length=40)
    link = discord.ui.TextInput(label="Instagram profile link", placeholder="instagram.com/janedoe", max_length=200)

    async def on_submit(self, interaction):
        await act_submit(interaction, self.username.value, self.link.value)


class TikTokModal(discord.ui.Modal, title="Onboard prospect"):
    tiktok = discord.ui.TextInput(label="Their TikTok username", max_length=40)
    email = discord.ui.TextInput(label="Email", required=False, max_length=120)
    phone = discord.ui.TextInput(label="Phone number", required=False, max_length=30)

    def __init__(self, ig_username: str):
        super().__init__()
        self.ig_username = ig_username

    async def on_submit(self, interaction):
        await act_link(interaction, self.tiktok.value, self.ig_username, self.email.value, self.phone.value)


class PendingPicker(discord.ui.View):
    """Pick a Pending prospect from a list instead of typing their Instagram name."""

    def __init__(self, subs):
        super().__init__(timeout=300)
        select = discord.ui.Select(placeholder="Choose a prospect", options=[
            discord.SelectOption(label=f"@{s['ig_username']}"[:100], value=s["ig_username"]) for s in subs[:25]])

        async def chosen(interaction: discord.Interaction):
            await interaction.response.send_modal(TikTokModal(select.values[0]))

        select.callback = chosen
        self.add_item(select)


class MonthModal(discord.ui.Modal, title="Scout payouts"):
    month = discord.ui.TextInput(label="Month (e.g. 202608) or leave empty for all unpaid", required=False, max_length=7)

    def __init__(self, handler=None):
        super().__init__()
        self.handler = handler or act_payouts

    async def on_submit(self, interaction):
        await self.handler(interaction, self.month.value.strip() or None)


class FileModal(discord.ui.Modal):
    def __init__(self, title, handler):
        super().__init__(title=title)
        self.handler = handler
        self.upload = discord.ui.FileUpload(required=True, min_values=1, max_values=1)
        self.add_item(discord.ui.Label(text="Choose the file (.csv or .xlsx)", component=self.upload))

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.handler(interaction, self.upload.values[0])


# ---------- panels (the buttons people actually press) ----------
class ScoutPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit a prospect", style=discord.ButtonStyle.primary, custom_id="panel:submit")
    async def submit_btn(self, interaction, button):
        await interaction.response.send_modal(SubmitModal())

    @discord.ui.button(label="My submissions", style=discord.ButtonStyle.secondary, custom_id="panel:mine")
    async def mine_btn(self, interaction, button):
        await act_mystatus(interaction)

    @discord.ui.button(label="My earnings", style=discord.ButtonStyle.secondary, custom_id="panel:earnings")
    async def earn_btn(self, interaction, button):
        await act_myearnings(interaction)

    @discord.ui.button(label="W-9 / tax form", style=discord.ButtonStyle.secondary, custom_id="panel:w9")
    async def w9_btn(self, interaction, button):
        await act_w9(interaction)


class AdminPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction):
        if not interaction.user.guild_permissions.administrator:
            await say(interaction, "Admins only.")
            return False
        return True

    @discord.ui.button(label="Import creators", style=discord.ButtonStyle.primary, custom_id="admin:creators", row=0)
    async def creators_btn(self, interaction, button):
        await interaction.response.send_modal(FileModal("Import creators file", act_import_creators))

    @discord.ui.button(label="Import earnings", style=discord.ButtonStyle.primary, custom_id="admin:earnings", row=0)
    async def earnings_btn(self, interaction, button):
        await interaction.response.send_modal(FileModal("Import monthly earnings", act_import_earnings))

    @discord.ui.button(label="Payouts", style=discord.ButtonStyle.success, custom_id="admin:payouts", row=0)
    async def payouts_btn(self, interaction, button):
        await interaction.response.send_modal(MonthModal())

    @discord.ui.button(label="Onboard prospect", style=discord.ButtonStyle.secondary, custom_id="admin:link", row=1)
    async def link_btn(self, interaction, button):
        pending = [s for s in core.pending_submissions(db())]
        if not pending:
            return await say(interaction, "No pending prospects right now.")
        note = "" if len(pending) <= 25 else f"\nShowing the first 25 of {len(pending)}. Use `/linkcreator` for the rest."
        await say(interaction, "Pick the prospect, then enter their TikTok username." + note, view=PendingPicker(pending))

    @discord.ui.button(label="Scouts", style=discord.ButtonStyle.secondary, custom_id="admin:scouts", row=1)
    async def scouts_btn(self, interaction, button):
        await act_roster(interaction, "scouts")

    @discord.ui.button(label="Creators", style=discord.ButtonStyle.secondary, custom_id="admin:creatorlist", row=1)
    async def creatorlist_btn(self, interaction, button):
        await act_roster(interaction, "creators")

    @discord.ui.button(label="Export approved", style=discord.ButtonStyle.primary, custom_id="admin:export", row=2)
    async def export_btn(self, interaction, button):
        await act_export_approved(interaction)

    @discord.ui.button(label="Export agreements", style=discord.ButtonStyle.secondary, custom_id="admin:agreements", row=2)
    async def agreements_btn(self, interaction, button):
        await act_export_agreements(interaction)

    @discord.ui.button(label="Export payouts", style=discord.ButtonStyle.success, custom_id="admin:exportpay", row=3)
    async def exportpay_btn(self, interaction, button):
        await interaction.response.send_modal(MonthModal(act_export_payouts))

    @discord.ui.button(label="Export W-9 list", style=discord.ButtonStyle.secondary, custom_id="admin:w9list", row=2)
    async def w9list_btn(self, interaction, button):
        await act_export_w9(interaction)

    @discord.ui.button(label="Schedule call", style=discord.ButtonStyle.success, custom_id="admin:schedule", row=2)
    async def schedule_btn(self, interaction, button):
        await interaction.response.send_modal(ScheduleModal())

    @discord.ui.button(label="Creator roster", style=discord.ButtonStyle.secondary, custom_id="admin:roster", row=3)
    async def roster_btn(self, interaction, button):
        await interaction.response.send_modal(FileModal("Upload creator roster (CSV)", creator_server.act_import_roster))

    @discord.ui.button(label="Live stats", style=discord.ButtonStyle.secondary, custom_id="admin:livestats", row=3)
    async def livestats_btn(self, interaction, button):
        await interaction.response.send_modal(FileModal("Upload TikTok LIVE stats (CSV)", creator_server.act_upload_stats))

    @discord.ui.button(label="Run nudges", style=discord.ButtonStyle.secondary, custom_id="admin:nudges", row=3)
    async def nudges_btn(self, interaction, button):
        await creator_server.act_run_nudges(interaction)

    @discord.ui.button(label="Weekly report", style=discord.ButtonStyle.secondary, custom_id="admin:weekly", row=3)
    async def weekly_btn(self, interaction, button):
        await creator_server.act_weekly_report(interaction)

    @discord.ui.button(label="Backup", style=discord.ButtonStyle.secondary, custom_id="admin:backup", row=1)
    async def backup_btn(self, interaction, button):
        await act_backup(interaction)


@bot.tree.command(name="setup", description="Post the scout panel here and the admin panel in #review")
@apply(admin_only)
async def setup_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.send(embed=scout_panel_embed(), view=ScoutPanel())
    await (await review_channel()).send(embed=admin_panel_embed(), view=AdminPanel())
    await say(interaction, "Panels posted.")


@bot.tree.command(name="setupapply", description="Post the 'Apply to be a scout' button here (the welcome channel)")
@apply(admin_only)
async def setupapply(interaction: discord.Interaction):
    await interaction.channel.send(embed=welcome_embed(), view=ApplyPanel())
    await say(interaction, "Posted.")


@bot.tree.command(name="agreement", description="Read the Scout Agreement")
@app_commands.guild_only()
async def agreement_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embeds=agreement_embeds(), ephemeral=True)


@bot.tree.command(name="setupguide", description="Post the scout guide (how the tools work + how to recruit) here")
@apply(admin_only)
async def setupguide(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    for e in guide_embeds():
        await interaction.channel.send(embed=e)
    await say(interaction, "Guide posted.")


# ---------- slash commands (optional shortcuts; the buttons do the same things) ----------
@bot.tree.command(name="submit", description="Submit a prospect: Instagram username + profile link")
@app_commands.guild_only()
async def submit(interaction: discord.Interaction, username: str, link: str):
    await act_submit(interaction, username, link)


@bot.tree.command(name="mystatus", description="Your submissions and where they stand")
async def mystatus(interaction: discord.Interaction):
    await act_mystatus(interaction)


@bot.tree.command(name="myearnings", description="Your earnings")
async def myearnings(interaction: discord.Interaction):
    await act_myearnings(interaction)


@bot.tree.command(name="importcreators", description="Upload TikTok's Manage creators file to update statuses")
@apply(admin_only)
async def importcreators(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    await act_import_creators(interaction, file)


@bot.tree.command(name="importearnings", description="Upload the monthly TikTok LIVE earnings export")
@apply(admin_only)
async def importearnings(interaction: discord.Interaction, file: discord.Attachment):
    await interaction.response.defer(ephemeral=True)
    await act_import_earnings(interaction, file)


@bot.tree.command(name="payouts", description="What each scout is owed")
@apply(admin_only)
async def payouts(interaction: discord.Interaction, month: str | None = None):
    await act_payouts(interaction, month)


@bot.tree.command(name="linkcreator", description="Link a TikTok username to the Instagram prospect, with contact details")
@apply(admin_only)
async def linkcreator(interaction: discord.Interaction, tiktok: str, instagram: str,
                      email: str | None = None, phone: str | None = None):
    await act_link(interaction, tiktok, instagram, email, phone)


@bot.tree.command(name="assignmanager", description="Manually set a creator's manager (normally automatic from the booking)")
@apply(admin_only)
async def assignmanager(interaction: discord.Interaction, tiktok: str, manager_email: str, manager_name: str = ""):
    conn = db()
    if conn.execute("SELECT 1 FROM creators WHERE handle=?", (core.norm_handle(tiktok),)).fetchone() is None:
        return await say(interaction, f"@{core.norm_handle(tiktok)} isn't registered yet.")
    core.assign_manager(conn, tiktok, manager_email, manager_name or None)
    await say(interaction, f"@{core.norm_handle(tiktok)}'s manager set to {manager_name or manager_email}.")


@bot.tree.command(name="exportapproved", description="CSV of prospects you approved (new since last export)")
@apply(admin_only)
async def exportapproved(interaction: discord.Interaction, everyone: bool = False):
    await act_export_approved(interaction, everyone)


@bot.tree.command(name="exportagreements", description="Zip of every scout's accepted agreement (for your records)")
@apply(admin_only)
async def exportagreements(interaction: discord.Interaction):
    await act_export_agreements(interaction)


@bot.tree.command(name="exportw9list", description="CSV (name, email) of approved scouts who still need a W-9")
@apply(admin_only)
async def exportw9list(interaction: discord.Interaction):
    await act_export_w9(interaction)


@bot.tree.command(name="exportpayouts", description="CSV of name + amount owed, only for scouts whose paperwork is on file")
@apply(admin_only)
async def exportpayouts(interaction: discord.Interaction, month: str | None = None):
    await act_export_payouts(interaction, month)


@bot.tree.command(name="removescout", description="Remove a scout (no new submissions). Residuals continue unless it's for breach.")
@apply(admin_only)
async def removescout(interaction: discord.Interaction, scout: discord.Member, for_breach: bool = False):
    conn = db()
    try:
        core.remove_scout(conn, scout.id, for_breach)
    except ValueError as e:
        return await say(interaction, str(e))
    if SCOUT_ROLE_ID:
        try:
            await scout.remove_roles(discord.Object(id=int(SCOUT_ROLE_ID)))
        except (discord.HTTPException, ValueError):
            pass
    await dm_scout(scout.id, "Your scouting agreement with Hierarchy Music has ended. You can't submit new prospects.")
    await say(interaction, f"{scout.display_name} removed. " + (
        "Marked as removed for breach: **no commission for later months.** Earlier months stay payable."
        if for_breach else "Their 5% continues on artists they already submitted while those artists stay Effective."))


@bot.tree.command(name="schedulecall", description="Book an intro call: email, date, time, time zone")
@apply(admin_only)
async def schedulecall(interaction: discord.Interaction):
    await interaction.response.send_modal(ScheduleModal())


@bot.tree.command(name="addmanager", description="Add a manager whose calendar is checked for intro calls")
@apply(admin_only)
async def addmanager(interaction: discord.Interaction, email: str, name: str = ""):
    try:
        scheduler.add_manager(db(), email, name)
    except ValueError as e:
        return await say(interaction, str(e))
    await say(interaction, f"Added {name or email}. They must share their calendar (free/busy) with your organizer "
                           "Google account. Run `/managers` to check.")


@bot.tree.command(name="removemanager", description="Stop assigning calls to a manager")
@apply(admin_only)
async def removemanager(interaction: discord.Interaction, email: str):
    done = scheduler.remove_manager(db(), email)
    await say(interaction, "Removed." if done else "No manager with that email.")


@bot.tree.command(name="managers", description="List managers and whether their calendars are readable")
@apply(admin_only)
async def managers_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    rows = db().execute("SELECT email, name FROM managers WHERE active=1 ORDER BY email").fetchall()
    if not rows:
        return await interaction.followup.send("No managers yet. Use `/addmanager`.", ephemeral=True)
    emails = [r["email"] for r in rows]
    try:
        if not (G_ID and G_SECRET and G_REFRESH):
            raise scheduler.NotConnected()
        now = datetime.datetime.now(datetime.timezone.utc)
        busy = await asyncio.to_thread(lambda: scheduler.GoogleCalendar(G_ID, G_SECRET, G_REFRESH)
                                       .busy(emails, now, now + datetime.timedelta(days=1)))
    except scheduler.NotConnected:
        busy = None
    except Exception as e:
        return await interaction.followup.send(f"Google error: {e}", ephemeral=True)
    lines = []
    for r in rows:
        state = "Google not connected" if busy is None else ("calendar readable" if busy.get(r["email"]) is not None
                                                             else "NOT shared with your organizer account")
        lines.append(f"- {r['name'] or r['email']} ({r['email']}): {state}")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@bot.tree.command(name="cancelcall", description="Cancel a creator's intro call and remove the calendar event")
@apply(admin_only)
async def cancelcall(interaction: discord.Interaction, email: str):
    await interaction.response.defer(ephemeral=True, thinking=True)

    def work():
        conn = db()
        try:
            cal = scheduler.GoogleCalendar(G_ID, G_SECRET, G_REFRESH) if (G_ID and G_SECRET and G_REFRESH) else \
                type("Off", (), {"delete_event": lambda self, i: None})()
            scheduler.cancel_call(conn, cal, email)
        finally:
            conn.close()
    try:
        await asyncio.to_thread(work)
    except ValueError as e:
        return await interaction.followup.send(str(e), ephemeral=True)
    await interaction.followup.send("Call canceled and the invite removed.", ephemeral=True)


@bot.tree.command(name="schedulesettings", description="Manager working hours (in your org time zone) and call length")
@apply(admin_only)
async def schedulesettings(interaction: discord.Interaction, start_hour: int | None = None,
                           end_hour: int | None = None, call_minutes: int | None = None):
    conn = db()
    for k, v in (("work_start", start_hour), ("work_end", end_hour), ("call_minutes", call_minutes)):
        if v is not None:
            core.set_setting(conn, k, v)
    ws, we, cm = scheduler._settings(conn)
    await say(interaction, f"Calls are offered Mon-Fri {ws}:00-{we}:00 ({ORG_TZ.key}), {cm} minutes each.")


@bot.tree.command(name="adjust", description="Correct a month after TikTok finalizes it")
@apply(admin_only)
@app_commands.describe(handle="TikTok handle", month="e.g. 202608", change="Change in bonus, e.g. -25.50 or 40")
async def adjust(interaction: discord.Interaction, handle: str, month: str, change: float, note: str = ""):
    try:
        lines = core.add_adjustment(db(), handle, month, core.to_cents(change), note)
    except ValueError as e:
        return await say(interaction, str(e))
    await say(interaction, f"Adjustment recorded for @{core.norm_handle(handle)}: scout {core.money(lines[0][2])}")


@bot.tree.command(name="w9", description="Mark a scout's W-9 + Direct Deposit packet as on file (after they sign it in DocuSign)")
@apply(admin_only)
async def w9(interaction: discord.Interaction, scout: discord.Member, on_file: bool = True):
    conn = db()
    core.ensure_payee(conn, scout.id, scout.display_name)
    core.set_w9(conn, scout.id, on_file)
    await say(interaction, f"W-9 {'marked on file' if on_file else 'cleared'} for <@{scout.id}>.")


@bot.tree.command(name="w9assign", description="Link a completed DocuSign W-9 to a scout")
@apply(admin_only)
async def w9assign(interaction: discord.Interaction, envelope: str, scout: discord.Member):
    try:
        core.assign_w9_event(db(), envelope.strip(), scout.id)
    except ValueError as e:
        return await say(interaction, str(e))
    await say(interaction, f"W-9 `{envelope.strip()}` linked to <@{scout.id}> and marked on file.")


@bot.tree.command(name="setbenchmark", description="Set the monthly minimums creators should hit")
@apply(admin_only)
async def setbenchmark(interaction: discord.Interaction, valid_days: float = 0.0, hours: float = 0.0):
    conn = db()
    core.set_setting(conn, "min_valid_days", valid_days)
    core.set_setting(conn, "min_hours", hours)
    await say(interaction, f"Benchmark saved: {valid_days:g} valid days, {hours:g} hours per month.")


@bot.tree.command(name="checkin", description="Creators below benchmark for a month")
@apply(admin_only)
async def checkin(interaction: discord.Interaction, month: str):
    conn = db()
    try:
        low = core.below_benchmark(conn, month)
    except ValueError as e:
        return await say(interaction, str(e))
    if not low:
        return await say(interaction, "Everyone is meeting the benchmark.")
    lines = [f"@{c['handle']} (scout {label(conn, c['scout_id']) if c['scout_id'] else '-'}): {why}" for c, why in low]
    await say(interaction, embed=discord.Embed(title=f"Below benchmark, {month}", description="\n".join(lines)[:4000], color=GOLD))


@bot.tree.command(name="backup", description="Send yourself a copy of the database")
@apply(admin_only)
async def backup(interaction: discord.Interaction):
    await act_backup(interaction)


if __name__ == "__main__":
    bot.run(TOKEN)
