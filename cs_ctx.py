"""Shared handles for the creators-server modules (filled in once by creator_server.init)."""
import discord


class _Ctx:
    bot = None
    db = None                 # () -> sqlite connection
    say = None                # async reply that only the user sees
    review_channel = None     # async () -> the staff server's private review channel
    file_modal = None         # FileModal class from bot.py
    org_tz = None
    guild_id = None           # the creators server
    staff_guild_id = None
    nudge_channel_id = None   # optional separate staff channel for nudges; defaults to the review channel
    gold = 0xD4AF37
    no_pings = discord.AllowedMentions.none()
    max_upload = 5 * 1024 * 1024


ctx = _Ctx()

ROLE_PENDING, ROLE_MEMBER, ROLE_ARTIST, ROLE_MODEL = "Pending", "Member", "Artist", "Model"
ROLE_ADMIN, ROLE_MANAGER = "Admin", "Manager"


def init(**kw):
    for k, v in kw.items():
        setattr(ctx, k, v)


def configured():
    return bool(ctx.guild_id)


async def creators_guild():
    if not ctx.guild_id:
        return None
    return ctx.bot.get_guild(int(ctx.guild_id)) or await ctx.bot.fetch_guild(int(ctx.guild_id))


def _stored(key):
    from core import get_setting
    conn = ctx.db()
    try:
        return get_setting(conn, key)
    finally:
        conn.close()


def _store(key, value):
    from core import set_setting
    conn = ctx.db()
    try:
        set_setting(conn, key, value)
    finally:
        conn.close()


def get_role(guild, name):
    rid = _stored(f"cg:role:{name}")
    role = guild.get_role(int(rid)) if rid else None
    return role or discord.utils.get(guild.roles, name=name)


async def ensure_role(guild, name, **kw):
    role = get_role(guild, name)
    if role is None:
        role = await guild.create_role(name=name, reason="Hierarchy creators server setup", **kw)
    _store(f"cg:role:{name}", role.id)
    return role


def get_channel(guild, name):
    cid = _stored(f"cg:ch:{name}")
    ch = guild.get_channel(int(cid)) if cid else None
    return ch or discord.utils.get(guild.channels, name=name)


def remember_channel(name, channel):
    _store(f"cg:ch:{name}", channel.id)


async def staff_channel():
    """Where staff cards go: the nudge channel if set, otherwise the review channel."""
    if ctx.nudge_channel_id:
        try:
            return ctx.bot.get_channel(int(ctx.nudge_channel_id)) or await ctx.bot.fetch_channel(int(ctx.nudge_channel_id))
        except (discord.HTTPException, ValueError):
            pass
    return await ctx.review_channel()


def admin_check(interaction):
    return interaction.user.guild_permissions.administrator
