"""Studio booking for artists. Hierarchy Music makes an introduction only: no booking, no payment, and the bot never
interprets a studio's reply. A person reads it and taps Available / Suggest other time / Not available."""
import datetime
import re

import discord
from discord import ui

import creators
import creator_content as T
import messaging
import studio
from cs_apply import send_to_creator_discord
from cs_ctx import ctx, staff_channel, admin_check, creators_guild

STAFF_ONLY = ("avail", "alt", "na")


def panel_embed():
    studios = studio.load_studios()
    e = discord.Embed(title=T.STUDIO_TITLE, description=T.STUDIO_INTRO, color=ctx.gold)
    for s in studios.values():
        e.add_field(name=s["name"], value=studio.rate_lines(s)[:1000], inline=False)
    e.add_field(name="Good to know", value=studio.DISCLAIMER, inline=False)
    return e


class StudioPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        opts = studio.room_options(studio.load_studios())[:25]
        sel = ui.Select(custom_id="studio:pick", placeholder="Choose a studio or room", min_values=1, max_values=1,
                        options=[discord.SelectOption(label=label[:100], value=value[:100]) for value, label in opts]
                        or [discord.SelectOption(label="No studios yet", value="none|")])
        sel.callback = self.picked
        self.add_item(sel)

    async def picked(self, interaction):
        value = interaction.data["values"][0]
        studio_id, _, room = value.partition("|")
        conn = ctx.db()
        try:
            c = creators.creator_by_discord(conn, interaction.user.id)
            if not c or c["member_status"] != "member" or c["kind"] != "artist":
                return await ctx.say(interaction, "Studio booking help is for approved artists.")
            if studio_id not in studio.load_studios():
                return await ctx.say(interaction, "That studio isn't available right now.")
            if studio.open_count(conn, interaction.user.id) >= studio.MAX_OPEN:
                return await ctx.say(interaction, f"You already have {studio.MAX_OPEN} open studio requests. "
                                                  "Once one is finished or closed, you can send another.")
        finally:
            conn.close()
        await interaction.response.send_modal(RequestModal(studio_id, room))


class RequestModal(ui.Modal):
    def __init__(self, studio_id, room):
        s = studio.load_studios()[studio_id]
        super().__init__(title=f"Request: {s['name']}"[:45])
        self.studio_id, self.room = studio_id, room
        self.dates = ui.TextInput(label="Preferred dates and times", style=discord.TextStyle.paragraph, max_length=400,
                                  placeholder="e.g. Fridays after 2pm, or Nov 12 to 15")
        self.length = ui.TextInput(label="Session length", max_length=60, placeholder="e.g. 4 hours")
        self.needs = ui.TextInput(label="Engineer or gear needs (optional)", style=discord.TextStyle.paragraph,
                                  required=False, max_length=400)
        for i in (self.dates, self.length, self.needs):
            self.add_item(i)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        studios = studio.load_studios()
        s = studios[self.studio_id]
        conn = ctx.db()
        try:
            try:
                req_id = studio.create_request(conn, interaction.user.id, self.studio_id, self.room, self.dates.value,
                                               self.length.value, self.needs.value)
            except studio.RequestLimit as e:
                return await ctx.say(interaction, str(e))
            req = studio.get_request(conn, req_id)
            artist = creators.creator_by_discord(conn, interaction.user.id)
            first = creators.first_name(artist["full_name"] if artist else None, interaction.user.display_name)
            note = await _email_studio(conn, req, s, first)
            studio.update_request(conn, req_id, email_note=note, status="emailed" if note == "emailed" else "new")
            req = studio.get_request(conn, req_id)
            msg = await (await staff_channel()).send(embed=staff_embed(req, s, artist), view=staff_view(req_id),
                                                     allowed_mentions=ctx.no_pings)
            studio.update_request(conn, req_id, staff_msg_id=str(msg.id))
        finally:
            conn.close()
        await ctx.say(interaction, f"Request sent! 🎶 We'll reach out to **{s['name']}** and message you here as soon as we "
                                   "hear back. Remember: you book and pay the studio directly. Hierarchy Music is just making the introduction.")


async def _email_studio(conn, req, s, artist_first):
    if not s.get("booking_email"):
        return "No booking email is set for this studio in studios.json. Contact them yourself."
    if not messaging.mail_configured():
        return "Email isn't set up (MAIL_ADDRESS / MAIL_PASSWORD). Contact the studio yourself."
    try:
        await messaging.send_email(s["booking_email"], studio.email_subject(req, artist_first),
                                   studio.email_body(req, s, artist_first))
        return "emailed"
    except Exception as e:
        print(f"Couldn't email the studio: {e}")
        return f"The email failed to send ({e}). Contact the studio yourself."


STATUS_TEXT = {"new": "Waiting on staff", "emailed": "Emailed, waiting for the studio", "offered": "Offered to the artist",
               "booked": "Artist says they booked", "done": "Finished", "not_available": "Not available", "cancelled": "Cancelled"}


def staff_embed(req, s, artist, reply_override=None):
    e = discord.Embed(title=f"Studio request #{req['id']}: {s['name']}", color=ctx.gold)
    e.add_field(name="Artist", value=f"<@{req['discord_id']}>" + (f" (@{artist['handle']})" if artist else ""))
    e.add_field(name="Room", value=req["room"] or "not specified")
    e.add_field(name="Status", value=STATUS_TEXT.get(req["status"], req["status"]))
    e.add_field(name="Preferred dates", value=req["dates"][:1000], inline=False)
    e.add_field(name="Length", value=req["length"])
    e.add_field(name="Engineer / gear", value=(req["needs"] or "none listed")[:1000])
    if req["email_note"] and req["email_note"] != "emailed":
        e.add_field(name="⚠️ Email", value=req["email_note"][:1000], inline=False)
    reply = reply_override or req["reply_text"]
    if reply:
        e.add_field(name="Studio's reply (word for word)", value=f"```\n{reply[:900]}\n```", inline=False)
    if req["booked_date"]:
        e.add_field(name="Booked for", value=req["booked_date"])
    if req["session_happened"] is not None:
        e.add_field(name="Session happened?", value="Yes" if req["session_happened"] else "No")
    return e


def staff_view(req_id, disabled=False):
    v = ui.View(timeout=None)
    for act in STAFF_ONLY:
        v.add_item(StudioButton(req_id, act, disabled=disabled))
    return v


class StudioButton(ui.DynamicItem[ui.Button], template=r"book:(?P<id>\d+):(?P<act>avail|alt|na|booked|happened|nothappened)"):
    LABELS = {"avail": ("Available", discord.ButtonStyle.success), "alt": ("Suggest other time", discord.ButtonStyle.primary),
              "na": ("Not available", discord.ButtonStyle.danger), "booked": ("I've booked", discord.ButtonStyle.success),
              "happened": ("Yes, it happened", discord.ButtonStyle.success), "nothappened": ("No", discord.ButtonStyle.secondary)}

    def __init__(self, req_id, act, disabled=False):
        label, style = self.LABELS[act]
        super().__init__(ui.Button(label=label, style=style, custom_id=f"book:{req_id}:{act}", disabled=disabled))
        self.req_id, self.act = req_id, act

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["id"]), match["act"])

    async def interaction_check(self, interaction):
        conn = ctx.db()
        try:
            req = studio.get_request(conn, self.req_id)
        finally:
            conn.close()
        if req is None:
            await ctx.say(interaction, "That request isn't in the database anymore.")
            return False
        if self.act in STAFF_ONLY:
            if not admin_check(interaction):
                await ctx.say(interaction, "Admins only.")
                return False
        elif str(interaction.user.id) != req["discord_id"]:
            await ctx.say(interaction, "That one is for the artist who made the request.")
            return False
        return True

    async def callback(self, interaction):
        if self.act == "alt":
            return await interaction.response.send_modal(SuggestModal(self.req_id))
        if self.act == "booked":
            return await interaction.response.send_modal(BookedModal(self.req_id))
        await interaction.response.defer()
        if self.act == "avail":
            await make_offer(interaction, self.req_id)
        elif self.act == "na":
            await decline(interaction, self.req_id)
        else:
            await record_session(interaction, self.req_id, self.act == "happened")


async def _refresh_card(req_id, interaction=None, closed=False):
    conn = ctx.db()
    try:
        req = studio.get_request(conn, req_id)
        s = studio.load_studios().get(req["studio_id"])
        artist = conn.execute("SELECT * FROM creators WHERE discord_id=?", (req["discord_id"],)).fetchone()
        embed, view = staff_embed(req, s, artist), staff_view(req_id, disabled=closed)
    finally:
        conn.close()
    if interaction is not None and not interaction.response.is_done():
        return await interaction.response.edit_message(embed=embed, view=view)
    if interaction is not None and interaction.message and interaction.message.id and req["staff_msg_id"] == str(interaction.message.id):
        return await interaction.edit_original_response(embed=embed, view=view)
    if req["staff_msg_id"]:
        ch = await staff_channel()
        try:
            msg = await ch.fetch_message(int(req["staff_msg_id"]))
            await msg.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass


async def _artist_row(conn, req):
    return conn.execute("SELECT * FROM creators WHERE discord_id=?", (req["discord_id"],)).fetchone()


async def make_offer(interaction, req_id, staff_note=None):
    conn = ctx.db()
    try:
        req = studio.get_request(conn, req_id)
        s = studio.load_studios()[req["studio_id"]]
        artist = await _artist_row(conn, req)
        view = ui.View(timeout=None)
        view.add_item(StudioButton(req_id, "booked"))
        sent = await send_to_creator_discord(artist or {"discord_id": req["discord_id"], "checkin_channel_id": None},
                                             studio.offer_text(s, req, staff_note), view=view)
        studio.update_request(conn, req_id, status="offered")
    finally:
        conn.close()
    await _refresh_card(req_id, interaction)
    if not sent:
        await interaction.followup.send("⚠️ I couldn't message the artist (DMs closed and no private channel). "
                                        "Please pass the booking details on yourself.", ephemeral=True)


async def decline(interaction, req_id):
    conn = ctx.db()
    try:
        req = studio.get_request(conn, req_id)
        s = studio.load_studios()[req["studio_id"]]
        artist = await _artist_row(conn, req)
        studio.update_request(conn, req_id, status="not_available")
    finally:
        conn.close()
    await send_to_creator_discord(artist or {"discord_id": req["discord_id"], "checkin_channel_id": None},
                                  f"Thanks for your patience! Unfortunately **{s['name']}** can't take that request. "
                                  "You're welcome to send another with different dates or a different studio.")
    await _refresh_card(req_id, interaction, closed=True)


class SuggestModal(ui.Modal, title="Suggest another time"):
    def __init__(self, req_id):
        super().__init__()
        self.req_id = req_id
        self.note = ui.TextInput(label="What the studio suggested", style=discord.TextStyle.paragraph, max_length=400)
        self.add_item(self.note)

    async def on_submit(self, interaction):
        await interaction.response.defer()
        await make_offer(interaction, self.req_id, staff_note=self.note.value)


def parse_date(text):
    t = str(text).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    raise ValueError("Please enter the date like 2026-11-14 or 11/14/2026.")


class BookedModal(ui.Modal, title="Your session date"):
    def __init__(self, req_id):
        super().__init__()
        self.req_id = req_id
        self.date = ui.TextInput(label="Date of your session", placeholder="11/14/2026", max_length=20)
        self.add_item(self.date)

    async def on_submit(self, interaction):
        try:
            d = parse_date(self.date.value)
        except ValueError as e:
            return await ctx.say(interaction, str(e))
        conn = ctx.db()
        try:
            studio.mark_booked(conn, self.req_id, d.isoformat())
        finally:
            conn.close()
        await ctx.say(interaction, f"Got it! Have a great session on **{d.strftime('%B %d, %Y')}**. 🎙️ "
                                   "We'll check in afterwards.")
        await _refresh_card(self.req_id)
        await (await staff_channel()).send(f"📅 Studio request #{self.req_id}: the artist says they booked for {d.isoformat()}.",
                                           allowed_mentions=ctx.no_pings)


async def record_session(interaction, req_id, happened):
    conn = ctx.db()
    try:
        studio.record_session(conn, req_id, happened)
    finally:
        conn.close()
    await interaction.edit_original_response(content="Thanks for letting us know!", view=None)
    await (await staff_channel()).send(f"{'✅' if happened else '❌'} Studio request #{req_id}: session "
                                       f"{'happened' if happened else 'did not happen'}.", allowed_mentions=ctx.no_pings)
    await _refresh_card(req_id)


# ---------- background: studio replies by email, and the "did it happen?" follow-up ----------
async def poll_replies():
    if not messaging.mail_configured():
        return
    replies = await messaging.fetch_replies()
    for req_id, sender, text, mid in replies:
        conn = ctx.db()
        try:
            key = mid or f"{req_id}:{hash(text)}"
            if conn.execute("SELECT 1 FROM seen_mail WHERE message_id=?", (key,)).fetchone():
                continue
            conn.execute("INSERT INTO seen_mail(message_id, at) VALUES (?,?)", (key, creators.utcnow().isoformat()))
            conn.commit()
            req = studio.get_request(conn, req_id)
            if req is None:
                continue
            body = (text or "(the reply had no text)")[:1800]
            studio.update_request(conn, req_id, reply_text=body)
        finally:
            conn.close()
        await _refresh_card(req_id)
        await (await staff_channel()).send(f"📬 **{sender}** replied to studio request #{req_id}. Read it on the request card "
                                           "and tap Available, Suggest other time or Not available.",
                                           allowed_mentions=ctx.no_pings)


async def send_followups():
    today = datetime.datetime.now(ctx.org_tz).date().isoformat()
    conn = ctx.db()
    try:
        due = studio.due_followups(conn, today)
        for req in due:
            s = studio.load_studios().get(req["studio_id"], {"name": "the studio"})
            artist = await _artist_row(conn, req)
            view = ui.View(timeout=None)
            view.add_item(StudioButton(req["id"], "happened"))
            view.add_item(StudioButton(req["id"], "nothappened"))
            await send_to_creator_discord(artist or {"discord_id": req["discord_id"], "checkin_channel_id": None},
                                          f"Hi! Did your session at **{s['name']}** on {req['booked_date']} happen? "
                                          "This helps us keep a record of referrals.", view=view)
            studio.update_request(conn, req["id"], asked_at=creators.utcnow().isoformat())
    finally:
        conn.close()
