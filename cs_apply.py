"""Creators server: applying, text verification, review cards, approval, access, reaching creators, inbound texts."""
import re
from xml.sax.saxutils import escape

import discord
from aiohttp import web
from discord import ui

import core
import creators
import creator_content as T
import messaging
from cs_ctx import (ctx, creators_guild, get_role, get_channel, admin_check, staff_channel,
                    ROLE_PENDING, ROLE_MEMBER, ROLE_ARTIST, ROLE_MODEL, ROLE_ADMIN, ROLE_MANAGER)

USERS_OK = discord.AllowedMentions(users=True, roles=False, everyone=False)
ROLE_HINT = ("I couldn't change their roles. In the creators server, open Server Settings → Roles and drag the bot's role "
             "above Member, Artist and Model, with Manage Roles turned on.")


# ---------- reaching creators ----------
async def _alert(text):
    ch = await ctx.review_channel()
    await ch.send(text[:1900], allowed_mentions=ctx.no_pings)


async def send_to_creator_discord(creator, text, view=None):
    """A Discord DM, or their private channel if DMs are closed. Returns True if it was delivered."""
    uid = creator["discord_id"]
    if not uid:
        return False
    try:
        user = ctx.bot.get_user(int(uid)) or await ctx.bot.fetch_user(int(uid))
        await user.send(text, view=view)
        return True
    except discord.HTTPException:
        pass
    cid = creator["checkin_channel_id"]
    if cid:
        try:
            ch = ctx.bot.get_channel(int(cid)) or await ctx.bot.fetch_channel(int(cid))
            await ch.send(f"<@{uid}> {text}", view=view, allowed_mentions=USERS_OK)
            return True
        except discord.HTTPException:
            pass
    return False


async def contact_creator(creator, text, kind="account", subject="A message from Hierarchy Music"):
    """The one function that reaches a creator: SMS (unless opted out), then Discord, then email, then staff."""
    senders = {"sms": messaging.send_sms if messaging.sms_configured() else None,
               "discord": send_to_creator_discord,
               "email": messaging.send_email if messaging.mail_configured() else None,
               "alert": _alert}
    conn = ctx.db()
    try:
        return await creators.contact(conn, creator, text, kind, senders, subject)
    finally:
        conn.close()


# ---------- the apply panel ----------
def apply_embed():
    e = discord.Embed(title=T.APPLY_TITLE, description=T.APPLY_TEXT, color=ctx.gold)
    e.set_footer(text=T.APPLY_FOOTER)
    return e


class ApplyPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _go(self, interaction, kind):
        conn = ctx.db()
        try:
            why = creators.apply_block_reason(conn, interaction.user.id)
        finally:
            conn.close()
        if why:
            return await ctx.say(interaction, why)
        await interaction.response.send_modal(ApplyModal(kind))

    @ui.button(label="Apply as Artist", style=discord.ButtonStyle.primary, custom_id="capply:artist")
    async def artist_btn(self, interaction, button):
        await self._go(interaction, "artist")

    @ui.button(label="Apply as Model", style=discord.ButtonStyle.primary, custom_id="capply:model")
    async def model_btn(self, interaction, button):
        await self._go(interaction, "model")

    @ui.button(label="I have a code", style=discord.ButtonStyle.secondary, custom_id="capply:code")
    async def code_btn(self, interaction, button):
        conn = ctx.db()
        try:
            app = creators.latest_app(conn, interaction.user.id)
        finally:
            conn.close()
        if not app or app["status"] != "verifying":
            return await ctx.say(interaction, "Start with **Apply as Artist** or **Apply as Model**, then you'll get a code.")
        await interaction.response.send_modal(CodeModal(app["id"]))


class ApplyModal(ui.Modal):
    def __init__(self, kind):
        super().__init__(title=f"Apply as {'an Artist' if kind == 'artist' else 'a Model'}")
        self.kind = kind
        self.f_name = ui.TextInput(label="Full name", max_length=80)
        self.tiktok = ui.TextInput(label="TikTok handle", placeholder="@yourhandle", max_length=60)
        self.insta = ui.TextInput(label="Instagram handle", placeholder="@yourhandle", max_length=60)
        self.email = ui.TextInput(label="Email", placeholder="you@example.com", max_length=120)
        self.phone = ui.TextInput(label="Mobile number (we'll text a code)", placeholder="(305) 555-0100", max_length=25)
        for item in (self.f_name, self.tiktok, self.insta, self.email, self.phone):
            self.add_item(item)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        conn = ctx.db()
        try:
            app = creators.start_application(conn, interaction.user.id, self.kind, self.f_name.value, self.tiktok.value,
                                             self.insta.value, self.email.value, self.phone.value)
        except (creators.ApplyBlocked, ValueError) as e:
            return await ctx.say(interaction, str(e))
        finally:
            conn.close()
        if not messaging.sms_configured():
            return await finalize(interaction, app["id"], note="text verification isn't switched on")
        await send_code(interaction, app["id"])


class CodeView(ui.View):
    def __init__(self, app_id):
        super().__init__(timeout=900)
        self.app_id = app_id

    async def interaction_check(self, interaction):
        conn = ctx.db()
        try:
            app = creators.get_app(conn, self.app_id)
        finally:
            conn.close()
        return bool(app) and str(interaction.user.id) == app["discord_id"]

    @ui.button(label="Enter code", style=discord.ButtonStyle.primary)
    async def enter(self, interaction, button):
        await interaction.response.send_modal(CodeModal(self.app_id))

    @ui.button(label="Send a new code", style=discord.ButtonStyle.secondary)
    async def resend(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await send_code(interaction, self.app_id)


class CodeModal(ui.Modal, title="Enter your code"):
    def __init__(self, app_id):
        super().__init__()
        self.app_id = app_id
        self.code = ui.TextInput(label="6-digit code", min_length=4, max_length=10, placeholder="123456")
        self.add_item(self.code)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        conn = ctx.db()
        try:
            app = creators.get_app(conn, self.app_id)
            if not app or str(interaction.user.id) != app["discord_id"]:
                return await ctx.say(interaction, "That application isn't yours.")
            result, left = creators.check_code(conn, self.app_id, self.code.value)
        finally:
            conn.close()
        if result == "ok":
            return await finalize(interaction, self.app_id)
        msg = {"bad": f"That code isn't right. You have {left} {'try' if left == 1 else 'tries'} left.",
               "expired": "That code has expired. Tap **Send a new code**.",
               "locked": "Too many wrong tries. Tap **Send a new code** to get a fresh one.",
               "none": "There's no active code. Tap **Send a new code**."}[result]
        await ctx.say(interaction, msg, view=CodeView(self.app_id))


async def send_code(interaction, app_id):
    conn = ctx.db()
    try:
        app = creators.get_app(conn, app_id)
        if creators.is_opted_out(conn, app["phone"]):      # STOP is always honoured, even for a verification text
            return await finalize(interaction, app_id, note="this number has opted out of texts")
        try:
            code = creators.issue_code(conn, app_id)
        except creators.CodeLimit as e:
            return await ctx.say(interaction, str(e))
    finally:
        conn.close()
    body = (f"Hierarchy Music: your code is {code} (valid 10 min). Reply STOP to opt out, HELP for help. "
            "Msg & data rates may apply.")
    try:
        await messaging.send_sms(app["phone"], body)
    except messaging.BadNumber:
        conn = ctx.db()
        conn.execute("DELETE FROM creator_apps WHERE id=?", (app_id,))
        conn.commit()
        conn.close()
        return await ctx.say(interaction, "We couldn't text that number. Please check it and apply again.")
    except creators.SmsBlocked:
        conn = ctx.db()
        creators.opt_out(conn, app["phone"], "provider block")
        conn.close()
        return await finalize(interaction, app_id, note="this number can't receive our texts")
    except Exception as e:
        print(f"Couldn't send a verification text: {e}")
        return await finalize(interaction, app_id, note="the verification text couldn't be sent")
    await ctx.say(interaction, f"We texted a 6-digit code to **{creators.masked(app['phone'])}**. "
                               "It's valid for 10 minutes. Tap **Enter code** when you have it.", view=CodeView(app_id))


# ---------- deciding ----------
async def finalize(interaction, app_id, note=None):
    """Phone step done (or skipped): auto-approve if every rule holds, otherwise send a card to the staff server."""
    conn = ctx.db()
    try:
        app = creators.get_app(conn, app_id)
        ev = creators.evaluate(conn, app)
        if note:
            ev["problems"].append(note)
            ev["decision"] = "review"
        first = creators.first_name(app["full_name"])
        if ev["decision"] == "auto":
            ok, msg = await approve_application(app_id, "auto")
            if ok:
                return await ctx.say(interaction, f"You're in, {first}! 🎉 Check your DMs and the new channels on the left.")
            ev["problems"].append(msg)
        creators.set_app_status(conn, app_id, "review", reason="; ".join(ev["problems"]))
        app = creators.get_app(conn, app_id)
    finally:
        conn.close()
    await post_review_card(app, ev)
    await ctx.say(interaction, T.review_dm(first))


def review_embed(app, ev, decision_line=None, color=None):
    e = discord.Embed(title=f"Creator application: {app['full_name']}", color=color or ctx.gold)
    e.add_field(name="Applying as", value=app["kind"].capitalize())
    e.add_field(name="Discord", value=f"<@{app['discord_id']}>")
    e.add_field(name="Mobile", value=f"{app['phone']} {'✅ verified' if app['phone_verified'] else '❌ not verified'}")
    e.add_field(name="TikTok", value=f"[@{app['tiktok']}](https://www.tiktok.com/@{app['tiktok']})")
    e.add_field(name="Instagram", value=f"[@{app['instagram']}](https://instagram.com/{app['instagram']})")
    e.add_field(name="Email", value=app["email"])
    e.add_field(name="Matched what we have on file", value=", ".join(ev["matched"]) or "nothing", inline=False)
    if ev["problems"]:
        e.add_field(name="Why a person needs to decide", value="\n".join(f"• {p}" for p in ev["problems"])[:1000], inline=False)
    if ev.get("scout_id"):
        e.add_field(name="Referred by scout", value=f"<@{ev['scout_id']}>", inline=False)
    if decision_line:
        e.add_field(name="Decision", value=decision_line, inline=False)
    e.set_footer(text=f"Application #{app['id']}")
    return e


def decision_view(app_id, current=None):
    v = ui.View(timeout=None)
    for d in ("approved", "denied"):
        v.add_item(CAppButton(app_id, d, current))
    return v


async def post_review_card(app, ev):
    ch = await ctx.review_channel()
    msg = await ch.send(embed=review_embed(app, ev), view=decision_view(app["id"]), allowed_mentions=ctx.no_pings)
    conn = ctx.db()
    conn.execute("UPDATE creator_apps SET review_msg_id=? WHERE id=?", (str(msg.id), app["id"]))
    conn.commit()
    conn.close()


class CAppButton(ui.DynamicItem[ui.Button], template=r"capp:(?P<id>\d+):(?P<d>approved|denied)"):
    def __init__(self, app_id, decision, current=None):
        style = (discord.ButtonStyle.success if decision == "approved" else discord.ButtonStyle.danger) \
            if decision == current else discord.ButtonStyle.secondary
        super().__init__(ui.Button(label="Approve" if decision == "approved" else "Deny", style=style,
                                   custom_id=f"capp:{app_id}:{decision}", disabled=current is not None))
        self.app_id, self.decision = app_id, decision

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["id"]), match["d"])

    async def interaction_check(self, interaction):
        if not admin_check(interaction):
            await ctx.say(interaction, "Admins only.")
            return False
        return True

    async def callback(self, interaction):
        await interaction.response.defer()
        conn = ctx.db()
        try:
            app = creators.get_app(conn, self.app_id)
            if app is None:
                return await ctx.say(interaction, "That application isn't in the database anymore.")
            if app["status"] in ("approved", "denied"):
                return await ctx.say(interaction, f"That application was already {app['status']}.")
            who = str(interaction.user.id)
            if self.decision == "approved":
                ok, msg = await approve_application(self.app_id, who)
                line = f"✅ Approved by <@{who}>" + (f"\n{msg}" if msg else "")
                color = 0x2ECC71 if ok else ctx.gold
                if not ok:
                    return await ctx.say(interaction, f"I couldn't approve that one: {msg}")
            else:
                await deny_application(self.app_id, who)
                line, color = f"❌ Denied by <@{who}>", 0xE74C3C
            app = creators.get_app(conn, self.app_id)
            ev = creators.evaluate(conn, app)
        finally:
            conn.close()
        await interaction.edit_original_response(embed=review_embed(app, ev, line, color),
                                                 view=decision_view(self.app_id, self.decision))


def _staff_roles(guild):
    return [r for r in (get_role(guild, ROLE_ADMIN), get_role(guild, ROLE_MANAGER)) if r]


async def _grant_roles(guild, member, kind, warnings):
    add = [r for r in (get_role(guild, ROLE_MEMBER), get_role(guild, ROLE_ARTIST if kind == "artist" else ROLE_MODEL)) if r]
    if len(add) < 2:
        warnings.append("The Member / Artist / Model roles don't exist yet. Run /setup-server in the creators server.")
    try:
        pending = get_role(guild, ROLE_PENDING)
        if pending and pending in member.roles:
            await member.remove_roles(pending, reason="Application approved")
        if add:
            await member.add_roles(*add, reason="Application approved")
    except discord.Forbidden:
        warnings.append(ROLE_HINT)
    except discord.HTTPException as e:
        warnings.append(f"Role change failed: {e}")


async def ensure_checkin_channel(guild, creator, member, warnings):
    """A private channel for this creator: visible only to them, admins and managers."""
    name = "checkin-" + re.sub(r"[^a-z0-9_-]", "-", creator["handle"])[:80]
    ch = None
    if creator["checkin_channel_id"]:
        ch = guild.get_channel(int(creator["checkin_channel_id"]))
    ch = ch or discord.utils.get(guild.text_channels, name=name)
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
          guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True,
                                                read_message_history=True, manage_channels=True)}
    if member:
        ow[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                                 attach_files=True)
    for r in _staff_roles(guild):
        ow[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                            manage_messages=True)
    try:
        if ch is None:
            if len(guild.channels) >= 490:
                warnings.append("The server is almost at Discord's 500-channel limit, so I didn't create a check-in channel.")
                return None
            cat = discord.utils.get(guild.categories, name="CHECK-INS")
            ch = await guild.create_text_channel(name, category=cat, overwrites=ow,
                                                 topic="Private check-ins between you and the Hierarchy Music team.")
        else:
            await ch.edit(overwrites=ow)
    except discord.HTTPException as e:
        warnings.append(f"Couldn't create the check-in channel: {e}")
        return None
    conn = ctx.db()
    creators.set_checkin_channel(conn, creator["handle"], ch.id)
    conn.close()
    return ch


async def approve_application(app_id, by):
    """Remove Pending, add Member + Artist/Model, make the private check-in channel, DM a welcome, say hello in #introductions."""
    guild = await creators_guild()
    if guild is None:
        return False, "The creators server isn't set up (CREATOR_GUILD_ID)."
    conn = ctx.db()
    warnings = []
    try:
        app = creators.get_app(conn, app_id)
        if app is None or app["status"] == "approved":
            return False, "already approved"
        member = None
        try:
            member = await guild.fetch_member(int(app["discord_id"]))
        except discord.HTTPException:
            return False, "They aren't in the creators server right now (they may have left). Ask them to rejoin, then approve again."
        creators.set_app_status(conn, app_id, "approved", decided_by=by)
        info = creators.register_member(conn, app)
        creator = conn.execute("SELECT * FROM creators WHERE handle=?", (info["handle"],)).fetchone()
        await _grant_roles(guild, member, app["kind"], warnings)
        ch = await ensure_checkin_channel(guild, creator, member, warnings)
        creator = conn.execute("SELECT * FROM creators WHERE handle=?", (info["handle"],)).fetchone()
        first = creators.first_name(app["full_name"])
        hello = T.welcome_dm(first, app["kind"])
        if not await send_to_creator_discord(creator, hello):
            warnings.append("I couldn't DM them; the welcome is in their private channel.")
        if ch:
            try:
                await ch.send(f"<@{app['discord_id']}> Welcome! This channel is private to you and the Hierarchy Music team. "
                              "We'll check in here, and any text replies you send us show up here too.",
                              allowed_mentions=USERS_OK)
            except discord.HTTPException:
                pass
        intro = get_channel(guild, "introductions")
        if intro:
            try:
                await intro.send(f"Please welcome **{first}** ({member.mention}), our newest **{app['kind'].capitalize()}**! 🎉",
                                 allowed_mentions=USERS_OK)
            except discord.HTTPException:
                pass
        extra = ""
        if info["scout_id"]:
            extra = " Linked to the scout who referred them."
        await _alert(f"Approved @{info['handle']} ({app['kind']}) {'automatically' if by == 'auto' else 'by <@' + str(by) + '>'}."
                     + extra + ("\n⚠️ " + "\n⚠️ ".join(warnings) if warnings else ""))
        return True, ("\n".join(warnings))
    finally:
        conn.close()


async def deny_application(app_id, by):
    conn = ctx.db()
    try:
        app = creators.set_app_status(conn, app_id, "denied", decided_by=by)
        creator = {"discord_id": app["discord_id"], "checkin_channel_id": None}
    finally:
        conn.close()
    await send_to_creator_discord(creator, T.denied_dm(creators.first_name(app["full_name"])))


# ---------- access follows TikTok status ----------
async def revoke_access(handle):
    conn = ctx.db()
    try:
        c = conn.execute("SELECT * FROM creators WHERE handle=?", (core.norm_handle(handle),)).fetchone()
        if not c or c["member_status"] != "member" or not c["discord_id"]:
            return
        guild = await creators_guild()
        if guild is None:
            return
        try:
            member = await guild.fetch_member(int(c["discord_id"]))
            roles = [r for r in (get_role(guild, n) for n in (ROLE_MEMBER, ROLE_ARTIST, ROLE_MODEL)) if r and r in member.roles]
            if roles:
                await member.remove_roles(*roles, reason="Terminated in TikTok")
            if c["checkin_channel_id"]:
                ch = guild.get_channel(int(c["checkin_channel_id"]))
                if ch:
                    await ch.set_permissions(member, overwrite=None)
        except discord.HTTPException:
            pass
        creators.set_member_status(conn, c["handle"], "removed")
    finally:
        conn.close()


async def restore_access(handle):
    conn = ctx.db()
    try:
        c = conn.execute("SELECT * FROM creators WHERE handle=?", (core.norm_handle(handle),)).fetchone()
        if not c or c["member_status"] != "removed" or not c["discord_id"]:
            return
        guild = await creators_guild()
        if guild is None:
            return
        try:
            member = await guild.fetch_member(int(c["discord_id"]))
        except discord.HTTPException:
            return
        warnings = []
        await _grant_roles(guild, member, c["kind"] or "artist", warnings)
        await ensure_checkin_channel(guild, c, member, warnings)
        creators.set_member_status(conn, c["handle"], "member")
    finally:
        conn.close()


async def on_status_change(handle, old, new):
    """Called after the creators import: Terminated removes access, Effective restores it for someone who was removed."""
    if new == "terminated":
        await revoke_access(handle)
    elif new == "effective":
        await restore_access(handle)


async def on_member_join(member):
    if not ctx.guild_id or str(member.guild.id) != str(ctx.guild_id):
        return
    conn = ctx.db()
    try:
        c = creators.creator_by_discord(conn, member.id)
    finally:
        conn.close()
    if c and c["member_status"] == "member":                 # an approved creator who left and came back
        warnings = []
        await _grant_roles(member.guild, member, c["kind"] or "artist", warnings)
        return
    pending = get_role(member.guild, ROLE_PENDING)
    if pending:
        try:
            await member.add_roles(pending, reason="New join")
        except discord.HTTPException:
            pass


# ---------- texts coming in (Twilio webhook) ----------
def _twiml(message=None):
    inner = f"<Message>{escape(message)}</Message>" if message else ""
    return web.Response(text=f"<Response>{inner}</Response>", content_type="text/xml")


async def sms_hook(request: web.Request):
    form = await request.post()
    params = {k: str(v) for k, v in form.items()}
    base = messaging.PUBLIC_BASE_URL or f"{request.headers.get('X-Forwarded-Proto', request.scheme)}://{request.host}"
    if not messaging.verify_twilio(base + request.path_qs, params, request.headers.get("X-Twilio-Signature", "")):
        return web.Response(status=403, text="bad signature")
    sender, body = params.get("From", ""), params.get("Body", "")
    conn = ctx.db()
    try:
        res = creators.handle_inbound(conn, sender, body, params.get("OptOutType"))
    finally:
        conn.close()
    c, kind = res["creator"], res["kind"]
    ch = None
    if c and c["checkin_channel_id"]:
        try:
            ch = ctx.bot.get_channel(int(c["checkin_channel_id"])) or await ctx.bot.fetch_channel(int(c["checkin_channel_id"]))
        except discord.HTTPException:
            ch = None
    who = f"@{c['handle']}" if c else f"an unknown number ({creators.masked(sender)})"
    try:
        if kind == "stop":
            note = (f"📵 {who} replied STOP. Texts are now off for them. I'll reach them by Discord, email and phone instead.")
            await _alert(note)
            if ch:
                await ch.send("📵 You replied STOP, so we've turned texts off. We'll reach you here and by email instead.",
                              allowed_mentions=ctx.no_pings)
        elif kind == "start":
            await _alert(f"✅ {who} replied START. Texts are back on for them.")
        elif kind == "help":
            return _twiml("Hierarchy Music: need help? Message the team on Discord"
                          + (f" or email {messaging.MAIL_ADDRESS}" if messaging.MAIL_ADDRESS else "")
                          + ". Reply STOP to opt out.")
        elif ch:
            first = creators.first_name(c["full_name"], c["handle"])
            await ch.send(f"📱 **{first}** replied by text:\n> " + body[:1500].replace("\n", "\n> "),
                          allowed_mentions=ctx.no_pings)
        else:
            await _alert(f"📱 Text from {who}:\n> " + body[:1500].replace("\n", "\n> "))
    except discord.HTTPException as e:
        print(f"Couldn't forward a text into Discord: {e}")
    return _twiml()
