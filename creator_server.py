"""Creators server: one entry point for bot.py. Everything that happens inside the creators server lives in the cs_* modules."""
import datetime
import json
import os

import discord
from discord import app_commands
from discord.ext import tasks

import academy
import core
import creators
import nudges
import messaging
import cs_ctx
from cs_ctx import ctx, get_channel
import cs_apply
import cs_academy
import cs_nudges
import cs_setup
import cs_studio
import cs_usernames
import cs_unlock

# re-exported for bot.py
on_member_join = cs_apply.on_member_join
on_status_change = cs_apply.on_status_change
act_import_roster = cs_nudges.act_import_roster
act_upload_stats = cs_nudges.act_upload_stats
act_run_nudges = cs_nudges.act_run_nudges
act_weekly_report = cs_nudges.act_weekly_report
configured = cs_ctx.configured
init = cs_ctx.init


def web_routes():
    return [("post", "/sms", cs_apply.sms_hook)]


def needs_web_server():
    return messaging.sms_configured()


async def _post_panel(channel, key, embed, view):
    """Post a panel once; later runs edit the same message."""
    if channel is None or not hasattr(channel, "send"):
        return None
    conn = ctx.db()
    try:
        mid = core.get_setting(conn, f"cg:post:{key}")
        if mid:
            try:
                msg = await channel.fetch_message(int(mid))
                await msg.edit(embed=embed, view=view)
                return msg
            except discord.HTTPException:
                pass
        msg = await channel.send(embed=embed, view=view)
        core.set_setting(conn, f"cg:post:{key}", msg.id)
        return msg
    finally:
        conn.close()


async def post_apply_panel(channel):
    return await _post_panel(channel, "apply", cs_apply.apply_embed(), cs_apply.ApplyPanelView())


async def post_studio_panel(channel):
    return await _post_panel(channel, "studio", cs_studio.panel_embed(), cs_studio.StudioPanelView())


async def post_username_panel(channel):
    return await _post_panel(channel, "username", cs_usernames.panel_embed(), cs_usernames.UsernamePanelView())


def register(bot):
    """Called once from bot.setup_hook. Registers the buttons, panels and commands."""
    bot.add_dynamic_items(cs_apply.CAppButton, cs_academy.LessonButton, cs_studio.StudioButton, cs_nudges.NudgeButton, cs_nudges.NudgeAllButton, cs_usernames.RequestButton)
    bot.add_view(cs_apply.ApplyPanelView())
    bot.add_view(cs_studio.StudioPanelView())
    bot.add_view(cs_usernames.UsernamePanelView())
    # ----- staff server commands (admin) -----
    @bot.tree.command(name="nudgeskip", description="Pause check-ins for a creator (on a break)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(handle="Their TikTok username", days="How many days to pause (blank = until you unpause)", note="Why")
    async def nudgeskip(interaction: discord.Interaction, handle: str, days: int | None = None, note: str = ""):
        until = (datetime.datetime.now(ctx.org_tz).date() + datetime.timedelta(days=days)).isoformat() if days else None
        conn = ctx.db()
        nudges.skip(conn, handle, until, note)
        conn.close()
        await ctx.say(interaction, f"Check-ins paused for @{core.norm_handle(handle)}" + (f" until {until}." if until else " until you unpause."))

    @bot.tree.command(name="manager-report", description="How each manager's creators are doing against the monthly minimums")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def manager_report(interaction: discord.Interaction):
        import managers
        cfg = nudges.load_config()
        conn = ctx.db()
        try:
            entries = managers.report(conn, cfg)
            r = managers.rules(cfg)
        finally:
            conn.close()
        if not entries:
            return await ctx.say(interaction, "No manager data yet. Upload the TikTok Live stats file first (it needs the \"Creator Network manager\" column).")
        e = discord.Embed(title="Manager report", color=ctx.gold,
                          description=f"Share of each manager's creators who reached the monthly minimum (valid days and hours). "
                                      f"Watch below {r['warn_pct']}%, danger below {r['fire_pct']}%. "
                                      f"Creators who joined under {r['min_tenure_days']} days ago and managers with under {r['min_creators']} creators are not judged.")
        e.add_field(name="Managers", value="\n".join(managers.line(g) for g in entries[:15])[:1000], inline=False)
        e.set_footer(text="A report only. Nothing happens to anyone automatically.")
        await ctx.say(interaction, embed=e)

    @bot.tree.command(name="nudgeunskip", description="Resume check-ins for a creator")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def nudgeunskip(interaction: discord.Interaction, handle: str):
        conn = ctx.db()
        nudges.unskip(conn, handle)
        conn.close()
        await ctx.say(interaction, f"Check-ins resumed for @{core.norm_handle(handle)}.")

    @bot.tree.command(name="setgoal", description="Set a creator's weekly LIVE goal in hours")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def setgoal(interaction: discord.Interaction, handle: str, hours: float):
        if not 0 < hours <= 168:
            return await ctx.say(interaction, "Hours per week must be between 0 and 168.")
        conn = ctx.db()
        nudges.set_goal(conn, handle, hours)
        conn.close()
        await ctx.say(interaction, f"Weekly goal for @{core.norm_handle(handle)} set to {hours:g} hours.")

    if not ctx.guild_id:
        cs_usernames.register(bot, None)
        return
    gobj = discord.Object(id=int(ctx.guild_id))
    cs_usernames.register(bot, gobj)
    cs_unlock.register(bot, gobj)

    # ----- creators server commands -----
    @bot.tree.command(name="setup-server", description="Build or update the roles, channels and permissions of this server", guild=gobj)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_server(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            notes = await cs_setup.build_server(interaction.guild, post_apply_panel, post_studio_panel, post_username_panel)
        except discord.Forbidden:
            return await ctx.say(interaction, "I don't have permission to do that. Give the bot's role **Administrator** in this "
                                              "server (or Manage Roles, Manage Channels and Manage Webhooks) and try again.")
        msg = "Done. Roles, channels and permissions are set up, and the welcome, rules, academy, apply, studio and username panels are posted, plus a private STAFF area (approvals, alerts, reports, backups)."
        msg += "\n\n**Next:** give yourself the **Admin** role (and managers the **Manager** role), and drag the bot's role to the top of " \
               "Server Settings → Roles so it can manage the roles below it."
        if notes:
            msg += "\n\n" + "\n".join(f"• {n}" for n in notes)
        await ctx.say(interaction, msg[:1900])

    @bot.tree.command(name="assign-manager", description="Link a manager's TikTok email to their Discord account so alerts reach them", guild=gobj)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(email="The manager's email exactly as TikTok shows it", user="Their Discord account", name="Their name (optional)")
    async def assign_manager(interaction: discord.Interaction, email: str, user: discord.Member, name: str = ""):
        import managers
        conn = ctx.db()
        try:
            try:
                e = managers.set_map(conn, email, user.id, name or user.display_name)
            except ValueError as ex:
                return await ctx.say(interaction, str(ex))
            n = conn.execute("SELECT COUNT(*) FROM live_stats WHERE manager=?", (e,)).fetchone()[0]
        finally:
            conn.close()
        await ctx.say(interaction, f"Linked {e} to {user.mention}. Follow-up alerts for their creators ({n} in the last file) will tag them.")

    @bot.tree.command(name="setup-apply", description="Post the application panel in this channel", guild=gobj)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_apply(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await post_apply_panel(interaction.channel)
        await ctx.say(interaction, "Application panel posted.")

    @bot.tree.command(name="setup-studio", description="Post the studio booking panel in this channel", guild=gobj)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_studio(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await post_studio_panel(interaction.channel)
        await ctx.say(interaction, "Studio panel posted.")

    @bot.tree.command(name="publish-lesson", description="Publish (or update) a lesson in #lessons", guild=gobj)
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(lesson="Which lesson file to publish")
    async def publish_lesson(interaction: discord.Interaction, lesson: str):
        await interaction.response.defer(ephemeral=True)
        try:
            thread, updated = await cs_academy.publish_lesson(interaction.guild, lesson)
        except ValueError as e:
            return await ctx.say(interaction, str(e))
        await ctx.say(interaction, f"{'Updated' if updated else 'Published'}: {thread.mention}")

    @publish_lesson.autocomplete("lesson")
    async def lesson_choices(interaction: discord.Interaction, current: str):
        try:
            lessons = academy.load_lessons()
        except ValueError:
            return []
        cur = current.lower()
        return [app_commands.Choice(name=f"Module {l['module']}: {l['title']}"[:100], value=l["id"])
                for l in lessons.values() if cur in l["title"].lower() or cur in l["id"]][:25]

    @bot.tree.command(name="progress", description="See your academy progress", guild=gobj)
    async def progress(interaction: discord.Interaction):
        conn = ctx.db()
        try:
            await ctx.say(interaction, embed=cs_academy.progress_embed(conn, interaction.user.id))
        finally:
            conn.close()


# ---------- background jobs (every 10 minutes) ----------
_started = False


async def _safe(label, coro):
    try:
        await coro
    except Exception as e:  # one failing job must never stop the others
        print(f"[creators] {label} failed: {e}")


async def _nightly_backup(now):
    """Once a night (after 3am), post a copy of the whole database to the private #staff-backups channel (or to the channel in
    BACKUP_CHANNEL_ID if you set one). Keeps the newest 14; older ones are deleted so personal details don't pile up in Discord."""
    if now.hour < 3:
        return
    cid = os.getenv("BACKUP_CHANNEL_ID")
    if cid:
        ch = ctx.bot.get_channel(int(cid)) or await ctx.bot.fetch_channel(int(cid))
    else:
        guild = await cs_ctx.creators_guild()
        ch = cs_ctx.get_channel(guild, "staff-backups") if guild else None
    if ch is None:
        return
    today = now.date().isoformat()
    conn = ctx.db()
    try:
        if core.get_setting(conn, "backup:last") == today:
            return
        import tempfile
        name = f"hierarchy-backup-{today}.db"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            core.backup_to(conn, path)
            if os.path.getsize(path) > 24 * 1024 * 1024:
                await ch.send("⚠️ The database is now too big to back up through Discord. Time to move backups elsewhere.")
            else:
                await ch.send(f"Nightly backup for {today}. Private: it contains creator contact details.", file=discord.File(path, filename=name))
        core.set_setting(conn, "backup:last", today)
    finally:
        conn.close()
    old = [m async for m in ch.history(limit=100) if m.author.id == ctx.bot.user.id and m.attachments
           and m.attachments[0].filename.startswith("hierarchy-backup-")]
    for m in old[14:]:
        try:
            await m.delete()
        except discord.HTTPException:
            pass


async def _tick():
    now = datetime.datetime.now(ctx.org_tz)
    await _safe("nightly backup", _nightly_backup(now))
    if not ctx.guild_id:
        return
    today, week = now.date().isoformat(), nudges.iso_week(now.date())
    try:
        cfg = nudges.load_config()
    except (OSError, ValueError) as e:
        print(f"[creators] nudge_rules.json problem: {e}")
        cfg = None
    conn = ctx.db()
    try:
        last_run = core.get_setting(conn, "nudge:last_run")
        last_report = core.get_setting(conn, "report:last_week")
        last_prompt = core.get_setting(conn, "prompt:last_week")
        has_stats = nudges.stats_age_days(conn) is not None
    finally:
        conn.close()
    if cfg:
        if now.hour >= cfg.get("run_hour_local", 10) and last_run != today and has_stats:
            await _safe("nudges", cs_nudges.run_nudges())
        if (now.weekday() == nudges.weekday_index(cfg.get("weekly_report_weekday", "Monday"))
                and now.hour >= cfg.get("weekly_report_hour_local", 9) and last_report != week and has_stats):
            await _safe("weekly report", cs_nudges.post_weekly_report())
    if now.weekday() == 0 and now.hour >= 9 and last_prompt != week:
        await _safe("weekly prompt", _post_weekly_prompt(week))
    await _safe("unlock check", cs_unlock.daily_check())
    await _safe("studio replies", cs_studio.poll_replies())
    await _safe("studio follow-ups", cs_studio.send_followups())


async def _post_weekly_prompt(week):
    guild = await cs_ctx.creators_guild()
    ch = get_channel(guild, "creator-lounge") if guild else None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.json")
    with open(path, encoding="utf-8") as f:
        prompts = json.load(f)
    conn = ctx.db()
    try:
        idx = int(core.get_setting(conn, "prompt:idx", "0")) % len(prompts)
        if ch is not None:
            await ch.send(f"☀️ **Monday prompt**\n{prompts[idx]}", allowed_mentions=ctx.no_pings)
            core.set_setting(conn, "prompt:idx", idx + 1)
            core.set_setting(conn, "prompt:last_week", week)
    finally:
        conn.close()


def start_loops(bot):
    global _started
    if _started or not ctx.guild_id:
        return
    _started = True

    @tasks.loop(minutes=10)
    async def tick():
        await _safe("background jobs", _tick())

    @tick.before_loop
    async def before():
        await bot.wait_until_ready()

    tick.start()
