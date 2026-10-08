"""Discord side of the unlock quota: gives the Unlocked (and Artist/Model) roles, tells the creator, alerts staff."""
import datetime

import discord
from discord import app_commands

import core
import creators
import nudges
import unlock
from cs_apply import contact_creator
from cs_ctx import (ctx, get_role, staff_channel, creators_guild, ROLE_UNLOCKED, ROLE_ARTIST, ROLE_MODEL,
                    ROLE_ARTIST_TRACK, ROLE_MODEL_TRACK)


async def grant_roles(guild, creator):
    """Unlocked + their Artist/Model access (and label, if missing). Returns True if the roles were given."""
    if not creator["discord_id"]:
        return False
    artist = (creator["kind"] or "artist") == "artist"
    names = (ROLE_UNLOCKED, ROLE_ARTIST_TRACK if artist else ROLE_MODEL_TRACK, ROLE_ARTIST if artist else ROLE_MODEL)
    roles = [r for r in (get_role(guild, n) for n in names) if r]
    if not roles:
        return False
    try:
        member = await guild.fetch_member(int(creator["discord_id"]))
        await member.add_roles(*roles, reason="Unlock quota reached")
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False


def unlocked_text(name):
    return (f"🎉 {name}, you're unlocked! You hit the LIVE-days goal, so the whole Hierarchy community and the academy are open to you now. "
            "Say hi in #creator-lounge. - Hierarchy Music")


async def run_checks():
    """Evaluate everyone after a stats upload (or the daily check). Returns a short summary for the caller."""
    cfg = nudges.load_config()
    conn = ctx.db()
    try:
        res = unlock.evaluate(conn, cfg, datetime.datetime.now(ctx.org_tz).date())
        rows = {h: conn.execute("SELECT * FROM creators WHERE handle=?", (h,)).fetchone() for h in res["unlocked"]}
        missed = [(conn.execute("SELECT * FROM creators WHERE handle=?", (h,)).fetchone(), p) for h, p in res["missed"]]
    finally:
        conn.close()
    guild = await creators_guild()
    done = 0
    for h, c in rows.items():
        if guild is not None and await grant_roles(guild, c):
            done += 1
        try:
            await contact_creator(c, unlocked_text(creators.first_name(c["full_name"], c["handle"])), "account", "You're unlocked!")
        except Exception as ex:
            print(f"[creators] unlock notice failed for {h}: {ex}", flush=True)
    if missed:
        ch = await staff_channel("alerts")
        lines = [f"@{c['handle']}: {int(p['days'])} of {p['need_days']} valid LIVE days after {unlock.rules(cfg)['window_days']}+ days" for c, p in missed]
        await ch.send("⏰ **Unlock quota missed** (still locked out of the full community). A manager should reach out, "
                      "or use /unlock-creator to open it up for them:\n" + "\n".join(lines)[:1800], allowed_mentions=ctx.no_pings)
    return {"unlocked": len(rows), "roles": done, "missed": len(missed)}


async def daily_check():
    cfg = nudges.load_config()
    if not unlock.rules(cfg)["enabled"]:
        return
    today = datetime.datetime.now(ctx.org_tz).date().isoformat()
    conn = ctx.db()
    try:
        if core.get_setting(conn, "unlock:last_check") == today or nudges.stats_age_days(conn) is None:
            return
        core.set_setting(conn, "unlock:last_check", today)
    finally:
        conn.close()
    await run_checks()


async def sync_roles(guild, notes):
    """Used by /setup-server: grandfather the current members once, then make sure every unlocked member has the roles."""
    cfg = nudges.load_config()
    conn = ctx.db()
    try:
        seeded = unlock.seed_existing(conn)
        todo = [c for c in creators.members(conn) if unlock.is_unlocked(conn, c["handle"], cfg)]
    finally:
        conn.close()
    for c in todo:
        await grant_roles(guild, c)
    if seeded:
        notes.append(f"{seeded} current member(s) keep full access. The quota applies to people approved from now on.")


def register(bot, gobj):
    if gobj is None:
        return

    @bot.tree.command(name="unlock-creator", description="Open the full community and academy for a creator now", guild=gobj)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(handle="Their TikTok username")
    async def unlock_creator(interaction: discord.Interaction, handle: str):
        await interaction.response.defer(ephemeral=True)
        conn = ctx.db()
        try:
            c = conn.execute("SELECT * FROM creators WHERE handle=? AND member_status='member'", (core.norm_handle(handle),)).fetchone()
            if c is None:
                return await ctx.say(interaction, "I couldn't find an active member with that username.")
            unlock.mark_unlocked(conn, c["handle"], interaction.user.id)
        finally:
            conn.close()
        ok = await grant_roles(interaction.guild, c)
        await ctx.say(interaction, f"@{c['handle']} is unlocked." + ("" if ok else " I couldn't change their Discord roles, so check the bot's role is above Unlocked."))
