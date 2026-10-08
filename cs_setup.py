"""/setup-server: builds the creators server (roles, categories, channels, permissions, AutoMod) and posts the standing messages.
Safe to run again: it finds what exists and updates it instead of making copies."""
import discord

import academy
import creator_content as T
from cs_ctx import (ctx, get_role, ensure_role, get_channel, remember_channel,
                    ROLE_PENDING, ROLE_MEMBER, ROLE_ARTIST, ROLE_MODEL, ROLE_ADMIN, ROLE_MANAGER, ROLE_UNLOCKED, ROLE_ARTIST_TRACK, ROLE_MODEL_TRACK)

PO = discord.PermissionOverwrite

# (category, [(channel name, type, who can see it, read-only?, slowmode seconds)])
# who: everyone | member | artist | model.   type: text | voice | stage | forum
LAYOUT = [
    ("START HERE", [("welcome", "text", "everyone", True, 0), ("apply", "text", "everyone", True, 0),
                   ("faqs", "text", "everyone", True, 0), ("support", "text", "everyone", False, 30)]),
    ("HIERARCHY", [("announcements", "text", "member", True, 0), ("rules", "text", "member", True, 0),
                   ("unlock-quota", "text", "member", True, 0),
                   ("introductions", "text", "member", False, 10), ("go-live-schedule", "text", "member", False, 10),
                   ("creator-lounge", "text", "unlocked", False, 5), ("wins", "text", "unlocked", False, 5),
                   ("collab-board", "text", "unlocked", False, 10), ("opportunities", "text", "unlocked", True, 0),
                   ("Lounge", "voice", "unlocked", False, 0)]),
    ("ACADEMY", [("academy-start", "text", "unlocked", True, 0), ("lessons", "forum", "unlocked", True, 0),
                 ("module-discussion", "text", "unlocked", False, 5), ("resources", "text", "unlocked", True, 0),
                 ("ask-for-help", "text", "unlocked", False, 5), ("office-hours", "stage", "unlocked", False, 0)]),
    ("CRAFT TRACKS", [("artist-lounge", "text", "artist", False, 5), ("song-feedback", "text", "artist", False, 10),
                      ("release-plans", "text", "artist", False, 10), ("book-studio", "text", "artist", True, 0),
                      ("model-lounge", "text", "model", False, 5), ("shoot-feedback", "text", "model", False, 10),
                      ("bookings", "text", "model", False, 10)]),
    ("CHECK-INS", [("weekly-goals", "text", "member", False, 10)]),
    # staff only: "admin" = the Admin role, "staff" = Admin and Manager
    ("STAFF", [("staff-approvals", "text", "admin", False, 0), ("staff-alerts", "text", "staff", False, 0),
               ("staff-reports", "text", "admin", False, 0), ("staff-backups", "text", "admin", False, 0)]),
]


def _who_roles(guild, who):
    if who == "everyone":
        return [guild.default_role]
    if who in ("admin", "staff"):
        return []                               # nobody but the staff roles added below
    name = {"member": ROLE_MEMBER, "unlocked": ROLE_UNLOCKED, "artist": ROLE_ARTIST_TRACK, "model": ROLE_MODEL_TRACK}[who]
    return [get_role(guild, name)]


CATEGORY_WHO = {"START HERE": ["everyone"], "HIERARCHY": ["member"], "ACADEMY": ["unlocked"],
                "CRAFT TRACKS": ["artist", "model"], "CHECK-INS": ["member"], "STAFF": ["staff"]}


def category_overwrites(guild, who_list):
    """Categories use the same visibility as what's inside them, so nothing shows up in the sidebar by mistake."""
    ow = {guild.default_role: PO(view_channel=False)}
    for who in who_list:
        for r in _who_roles(guild, who):
            if r is not None:
                ow[r] = PO(view_channel=True)
    for r in (get_role(guild, ROLE_ADMIN), get_role(guild, ROLE_MANAGER)):
        if r:
            ow[r] = PO(view_channel=True)
    if guild.me:
        ow[guild.me] = PO(view_channel=True, manage_channels=True)
    return ow


def overwrites(guild, kind, who, read_only):
    ow = {guild.default_role: PO(view_channel=False)}
    me = guild.me
    staff = [r for r in (get_role(guild, ROLE_ADMIN), None if who == "admin" else get_role(guild, ROLE_MANAGER)) if r]
    for r in _who_roles(guild, who):
        if r is None:
            continue
        if kind in ("voice", "stage"):
            ow[r] = PO(view_channel=True, connect=True, speak=(kind == "voice"), stream=True)
        elif kind == "forum":
            ow[r] = PO(view_channel=True, read_message_history=True, send_messages=False, create_public_threads=False,
                       send_messages_in_threads=True, add_reactions=True, attach_files=True, embed_links=True)
        else:
            ow[r] = PO(view_channel=True, read_message_history=True, send_messages=not read_only,
                       add_reactions=True, attach_files=not read_only, embed_links=True)
    for r in staff:
        ow[r] = PO(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True,
                   connect=True, speak=True, create_public_threads=True, send_messages_in_threads=True,
                   manage_threads=True)
    if me:
        ow[me] = PO(view_channel=True, send_messages=True, embed_links=True, read_message_history=True,
                    manage_channels=True, manage_threads=True, create_public_threads=True, send_messages_in_threads=True,
                    manage_messages=True, connect=True, speak=True)
    return ow


async def _ensure_channel(guild, category, name, kind, ow, slowmode, notes):
    ch = get_channel(guild, name)
    topic = T.TOPICS.get(name)
    if ch is not None:
        kw = {"overwrites": ow, "category": category}
        if kind in ("text", "forum") and topic:
            kw["topic"] = topic
        if kind == "text":
            kw["slowmode_delay"] = slowmode
        await ch.edit(**kw)
        remember_channel(name, ch)
        return ch
    if kind == "text":
        ch = await guild.create_text_channel(name, category=category, overwrites=ow, topic=topic, slowmode_delay=slowmode)
    elif kind == "voice":
        ch = await guild.create_voice_channel(name, category=category, overwrites=ow)
    elif kind == "stage":
        try:
            ch = await guild.create_stage_channel(name, category=category, overwrites=ow)
        except (discord.HTTPException, TypeError):
            notes.append("Stage channels need Community turned on, so #office-hours is a voice channel for now.")
            ch = await guild.create_voice_channel(name, category=category, overwrites=ow)
    else:  # forum
        try:
            ch = await guild.create_forum(name, category=category, overwrites=ow, topic=topic)
        except (discord.HTTPException, TypeError):
            notes.append("Forum channels need Community turned on (Server Settings → Enable Community), so #lessons is a "
                         "text channel for now. Lessons still work: each one gets its own thread.")
            ow2 = dict(ow)
            ch = await guild.create_text_channel(name, category=category, overwrites=ow2, topic=topic)
    remember_channel(name, ch)
    return ch


async def _post(guild, channel_name, key, embed, view=None):
    """Post once, edit the same message afterwards."""
    ch = get_channel(guild, channel_name)
    if ch is None or not hasattr(ch, "send"):
        return
    from core import get_setting, set_setting
    conn = ctx.db()
    try:
        mid = get_setting(conn, f"cg:post:{key}")
        if mid:
            try:
                msg = await ch.fetch_message(int(mid))
                await msg.edit(embed=embed, view=view)
                return
            except discord.NotFound:
                pass
        msg = await ch.send(embed=embed, view=view)
        set_setting(conn, f"cg:post:{key}", msg.id)
    finally:
        conn.close()


async def _automod(guild, notes):
    try:
        existing = {r.name for r in await guild.fetch_automod_rules()}
        action = discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message)
        if "Hierarchy: mention spam" not in existing:
            await guild.create_automod_rule(
                name="Hierarchy: mention spam", event_type=discord.AutoModRuleEventType.message_send,
                trigger=discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.mention_spam, mention_limit=5),
                actions=[action], enabled=True, reason="Creators server setup")
        if "Hierarchy: spam" not in existing:
            await guild.create_automod_rule(
                name="Hierarchy: spam", event_type=discord.AutoModRuleEventType.message_send,
                trigger=discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.spam),
                actions=[action], enabled=True, reason="Creators server setup")
    except (discord.HTTPException, TypeError, ValueError, AttributeError) as e:
        notes.append(f"I couldn't turn on AutoMod ({e}). You can add it in Server Settings → AutoMod.")


async def build_server(guild, post_apply, post_studio, post_username=None):
    """Create or update everything. Returns a list of notes for the admin."""
    notes = []
    await ensure_role(guild, ROLE_PENDING, colour=discord.Colour.light_grey())
    await ensure_role(guild, ROLE_MEMBER, colour=discord.Colour(ctx.gold), hoist=True)
    await ensure_role(guild, ROLE_UNLOCKED, colour=discord.Colour.green())
    await ensure_role(guild, ROLE_ARTIST_TRACK)
    await ensure_role(guild, ROLE_MODEL_TRACK)
    await ensure_role(guild, ROLE_ARTIST, colour=discord.Colour.purple(), hoist=True)
    await ensure_role(guild, ROLE_MODEL, colour=discord.Colour.magenta(), hoist=True)
    await ensure_role(guild, ROLE_ADMIN, colour=discord.Colour.red(), hoist=True)
    await ensure_role(guild, ROLE_MANAGER, colour=discord.Colour.blue())
    mods = sorted(academy.by_module(academy.load_lessons()))
    for m in mods:
        await ensure_role(guild, f"Module {m} Complete")
    for cat_name, chans in LAYOUT:
        cat = discord.utils.get(guild.categories, name=cat_name)
        if cat is None:
            cat = await guild.create_category(cat_name)
        for name, kind, who, ro, slow in chans:
            try:
                ow = overwrites(guild, kind, who, ro)
                await _ensure_channel(guild, cat, name, kind, ow, slow, notes)
            except discord.Forbidden:
                raise
            except (discord.HTTPException, TypeError, ValueError) as e:
                notes.append(f"I couldn't set up #{name} ({e}). You can create it by hand and run /setup-server again.")
        await cat.edit(overwrites=category_overwrites(guild, CATEGORY_WHO.get(cat_name, ["member"])))
    for ch_name, (title, text) in T.STAFF_NOTES.items():
        await _post(guild, ch_name, ch_name, discord.Embed(title=title, description=text, color=ctx.gold))
    await _post(guild, "welcome", "welcome", discord.Embed(title=T.WELCOME_TITLE, description=T.WELCOME_TEXT, color=ctx.gold))
    await _post(guild, "rules", "rules", discord.Embed(title=T.RULES_TITLE, description=T.RULES_TEXT, color=ctx.gold))
    await _post(guild, "academy-start", "academy", discord.Embed(title=T.ACADEMY_TITLE, description=T.ACADEMY_TEXT, color=ctx.gold))
    import nudges, unlock
    ur = unlock.rules(nudges.load_config())
    if ur["enabled"]:
        await _post(guild, "unlock-quota", "unlock", discord.Embed(title=T.UNLOCK_TITLE, description=T.unlock_text(ur), color=ctx.gold))
    faq = discord.Embed(title=T.FAQ_TITLE, color=ctx.gold)
    for q, a in T.FAQS:
        faq.add_field(name=q[:256], value=a[:1024], inline=False)
    await _post(guild, "faqs", "faqs", faq)
    await post_apply(get_channel(guild, "apply"))
    await post_studio(get_channel(guild, "book-studio"))
    if post_username is not None:
        await post_username(get_channel(guild, "support"))
    await _automod(guild, notes)
    import cs_unlock
    await cs_unlock.sync_roles(guild, notes)
    return notes
