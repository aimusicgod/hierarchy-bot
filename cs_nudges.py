"""Staff side of the creators server: roster and live-stats uploads, the nudge approval queue, the weekly report."""
import datetime

import discord
from discord import ui

import core
import creators
import nudges
import usernames
import managers
from cs_apply import contact_creator
import cs_ctx
from cs_ctx import ctx, staff_channel, admin_check, staff_check, get_channel


# ---------- uploads (buttons on the staff admin panel) ----------
async def act_import_roster(interaction, att):
    if att.size > ctx.max_upload:
        return await ctx.say(interaction, "File is too large.")
    conn = ctx.db()
    try:
        rep = creators.import_roster(conn, creators.parse_roster(await att.read(), att.filename))
    except ValueError as e:
        return await ctx.say(interaction, str(e))
    finally:
        conn.close()
    e = discord.Embed(title="Creator roster imported", color=ctx.gold,
                      description="These are the details used to auto-approve applications.")
    e.add_field(name="Added", value=str(rep["added"]))
    e.add_field(name="Updated", value=str(rep["updated"]))
    if rep["bad"]:
        e.add_field(name=f"Skipped ({len(rep['bad'])})", value="\n".join(rep["bad"][:15])[:1000], inline=False)
    await ctx.say(interaction, embed=e)


async def act_upload_stats(interaction, att):
    if att.size > ctx.max_upload:
        return await ctx.say(interaction, "File is too large.")
    cfg = nudges.load_config()
    conn = ctx.db()
    try:
        rows, found = nudges.parse_stats(await att.read(), att.filename, cfg)
        renamed, skipped = usernames.apply_export_renames(conn, rows)
        n = nudges.save_stats(conn, rows)
        managers.record_final(conn, managers.report(conn, cfg))
    except ValueError as e:
        return await ctx.say(interaction, str(e))
    finally:
        conn.close()
    e = discord.Embed(title="Live stats saved", color=ctx.gold,
                      description=f"{n} creators in the file. Check-ins use these numbers the next time they run (or tap **Run nudges**).")
    conn = ctx.db()
    try:
        row = conn.execute("SELECT period_start, period_end FROM live_stats WHERE period_end IS NOT NULL LIMIT 1").fetchone()
        act = nudges.agency_active(conn, cfg)
    finally:
        conn.close()
    if row:
        e.add_field(name="Data period", value=f"{row['period_start']} to {row['period_end']}")
    month_cols = {"diamonds", "live_hours", "valid_days"}
    if month_cols <= set(found):
        e.add_field(name="Monthly goals", value="Diamonds, LIVE duration and Valid go LIVE days found.")
    else:
        e.add_field(name="Missing", value=", ".join(sorted(k.replace("_", " ") for k in month_cols - set(found))) +
                    "\nMonthly alerts need these columns.", inline=False)
    if act:
        e.add_field(name="Active creators", value=_active_line(act), inline=False)
    conn = ctx.db()
    try:
        unl = managers.unlinked(conn)
    finally:
        conn.close()
    if unl:
        e.add_field(name="Managers not linked to Discord yet", value="\n".join(unl)[:900] + "\nUse /assign-manager so their alerts reach them.", inline=False)
    if renamed:
        e.add_field(name=f"Username changes found ({len(renamed)})", value="\n".join(f"@{o} is now @{n_}" for o, n_ in renamed)[:1000]
                    + "\nTheir records and history were moved to the new name.", inline=False)
    if skipped:
        e.add_field(name=f"Could not rename ({len(skipped)})", value="\n".join(f"@{o} to @{n_}: {why}" for o, n_, why in skipped)[:1000], inline=False)
    try:
        import cs_unlock
        res = await cs_unlock.run_checks()
        if res["unlocked"] or res["missed"]:
            e.add_field(name="Unlock quota", value=f"{res['unlocked']} newly unlocked, {res['missed']} past the deadline without it.", inline=False)
    except Exception as ex:                                     # never let this break the upload itself
        print(f"[creators] unlock check failed: {ex}", flush=True)
    await ctx.say(interaction, embed=e)


def _active_line(a):
    flag = {"danger": "🚨", "watch": "⚠️", "ok": "✅"}[a["level"]]
    return (f"{flag} {a['met']} of {a['total']} have reached the monthly active minimum ({a['now_pct']:.0f}%). "
            f"Best case if everyone who still can does: {a['possible']} of {a['total']} ({a['best_pct']:.0f}%). "
            f"Shut-down floor: {a['floor']}%.")


# ---------- the approval queue ----------
def escalation_embed(n, creator, status=None):
    e = discord.Embed(title=f"📞 Manager follow-up needed: @{n['handle']}", description=n["text"], color=0xC0392B)
    if creator is not None:
        e.add_field(name="Name", value=creator["full_name"] or "—")
        e.add_field(name="Phone", value=creator["phone"] or "—")
        e.add_field(name="Email", value=creator["email"] or "—")
        if creator["discord_id"]:
            e.add_field(name="Discord", value=f"<@{creator['discord_id']}>")
    conn = ctx.db()
    try:
        e.add_field(name="Their manager", value=managers.label(managers.assignment(conn, n["handle"])), inline=False)
    finally:
        conn.close()
    if status:
        e.add_field(name="Result", value=status, inline=False)
    e.set_footer(text=f"Alert #{n['id']}  |  Nothing is sent to the creator. Tap Contacted once a manager has reached them")
    return e


def nudge_embed(n, creator, rule_id, status=None):
    if nudges.is_escalation(rule_id):
        return escalation_embed(n, creator, status)
    e = discord.Embed(title=f"Check-in for @{n['handle']}", description=n["text"], color=ctx.gold)
    e.add_field(name="Rule", value=rule_id.replace("_", " "))
    if creator is not None:
        e.add_field(name="Name", value=creator["full_name"] or "—")
    if status:
        e.add_field(name="Result", value=status, inline=False)
    e.set_footer(text=f"Nudge #{n['id']}  |  Nothing is sent until you tap Send")
    return e


def nudge_view(nid, current=None, escalation=False):
    v = ui.View(timeout=None)
    for act in (("done", "skip") if escalation else ("send", "skip")):
        v.add_item(NudgeButton(nid, act, current))
    return v


_LABELS = {"send": "Send", "skip": "Skip", "done": "Contacted"}


class NudgeButton(ui.DynamicItem[ui.Button], template=r"nudge:(?P<id>\d+):(?P<act>send|skip|done)"):
    def __init__(self, nid, act, current=None):
        super().__init__(ui.Button(label=_LABELS[act],
                                   style=discord.ButtonStyle.success if act in ("send", "done") else discord.ButtonStyle.secondary,
                                   custom_id=f"nudge:{nid}:{act}", disabled=current is not None))
        self.nid, self.act = nid, act

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["id"]), match["act"])

    async def interaction_check(self, interaction):
        allowed = staff_check(interaction) if self.act == "done" else admin_check(interaction)   # managers can log their own follow-ups
        if not allowed:
            await ctx.say(interaction, "Admins only." if self.act != "done" else "Admins and managers only.")
            return False
        return True

    async def callback(self, interaction):
        await interaction.response.defer()
        conn = ctx.db()
        try:
            n = nudges.get_nudge(conn, self.nid)
            if n is None or n["status"] != "queued":
                return await ctx.say(interaction, "That check-in was already handled.")
            c = conn.execute("SELECT * FROM creators WHERE handle=?", (n["handle"],)).fetchone()
            if self.act == "skip":
                nudges.mark_nudge(conn, self.nid, "skipped", decided_by=str(interaction.user.id))
                result = f"Skipped by <@{interaction.user.id}>"
            elif self.act == "done":
                owner = managers.assignment(conn, n["handle"])["discord_id"]
                if owner and str(interaction.user.id) != owner and not admin_check(interaction):
                    return await ctx.say(interaction, f"This one belongs to <@{owner}>. Only they or an admin can mark it done.")
                nudges.mark_nudge(conn, self.nid, "sent", channel="manager", decided_by=str(interaction.user.id))
                result = f"Manager contact logged by <@{interaction.user.id}>"
            else:
                if c is None or c["member_status"] != "member":
                    return await ctx.say(interaction, "That creator isn't an active member anymore.")
                used = await contact_creator(c, n["text"], "checkin", "A check-in from Hierarchy Music")
                nudges.mark_nudge(conn, self.nid, "sent", channel=used, decided_by=str(interaction.user.id))
                result = f"Sent by <@{interaction.user.id}> via **{used}**"
            n = nudges.get_nudge(conn, self.nid)
        finally:
            conn.close()
        await interaction.edit_original_response(embed=nudge_embed(n, c, n["rule_id"], result),
                                                 view=nudge_view(self.nid, self.act, nudges.is_escalation(n["rule_id"])))


def nudge_all_view(week, done=None):
    v = ui.View(timeout=None)
    for act in ("send", "skip"):
        v.add_item(NudgeAllButton(week, act, done))
    return v


class NudgeAllButton(ui.DynamicItem[ui.Button], template=r"nudgeall:(?P<week>[0-9A-Za-z\-]+):(?P<act>send|skip)"):
    def __init__(self, week, act, done=None):
        super().__init__(ui.Button(label="Send all" if act == "send" else "Skip all",
                                   style=discord.ButtonStyle.success if act == "send" else discord.ButtonStyle.secondary,
                                   custom_id=f"nudgeall:{week}:{act}", disabled=done is not None))
        self.week, self.act = week, act

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["week"], match["act"])

    async def interaction_check(self, interaction):
        if not admin_check(interaction):
            await ctx.say(interaction, "Admins only.")
            return False
        return True

    async def callback(self, interaction):
        await interaction.response.defer()
        conn = ctx.db()
        sent = skipped = failed = 0
        try:
            rows = conn.execute("SELECT * FROM nudges WHERE week=? AND rule_id='weekly_update' AND status='queued'", (self.week,)).fetchall()
            for n in rows:
                c = conn.execute("SELECT * FROM creators WHERE handle=?", (n["handle"],)).fetchone()
                if self.act == "skip" or c is None or c["member_status"] != "member":
                    nudges.mark_nudge(conn, n["id"], "skipped", decided_by=str(interaction.user.id))
                    skipped += 1
                    continue
                try:
                    used = await contact_creator(c, n["text"], "checkin", "Your Hierarchy Music weekly update")
                    nudges.mark_nudge(conn, n["id"], "sent", channel=used, decided_by=str(interaction.user.id))
                    sent += 1
                except Exception as ex:
                    failed += 1
                    print(f"[creators] weekly update to {n['handle']} failed: {ex}", flush=True)
        finally:
            conn.close()
        result = f"Handled by <@{interaction.user.id}>: {sent} sent, {skipped} skipped" + (f", {failed} failed" if failed else "") + "."
        e = discord.Embed(title="Weekly progress updates", description=result, color=ctx.gold)
        await interaction.edit_original_response(embed=e, view=nudge_all_view(self.week, self.act))


async def run_nudges(force=False):
    """Build today's nudges and put them in the staff queue (or send them if auto_send is on). Returns a text summary."""
    cfg = nudges.load_config()
    today = datetime.datetime.now(ctx.org_tz).date()
    conn = ctx.db()
    try:
        new, notes = nudges.build_nudges(conn, cfg, today)
        ch = await staff_channel("approvals")
        alerts = await staff_channel("alerts")
        for note in notes:
            await ch.send(f"ℹ️ Check-ins: {note}", allowed_mentions=ctx.no_pings)
        batch = [n for n in new if n["rule_id"] == "weekly_update"]
        new_individual = [n for n in new if n["rule_id"] != "weekly_update"]
        if batch:
            week = nudges.iso_week(today)
            e = discord.Embed(title=f"Weekly progress updates ready ({len(batch)})", color=ctx.gold,
                              description="Each member gets their diamonds, valid LIVE days and hours for the month.\n\n**Preview:**\n" + batch[0]["text"])
            e.set_footer(text="Nothing is sent until you tap Send all")
            await ch.send(embed=e, view=nudge_all_view(week), allowed_mentions=ctx.no_pings)
        for n in new_individual:
            c = conn.execute("SELECT * FROM creators WHERE handle=?", (n["handle"],)).fetchone()
            esc = nudges.is_escalation(n["rule_id"])
            if cfg.get("auto_send") and not esc:
                used = await contact_creator(c, n["text"], "checkin", "A check-in from Hierarchy Music")
                nudges.mark_nudge(conn, n["id"], "sent", channel=used, decided_by="auto")
                await ch.send(f"Auto-sent a check-in to @{n['handle']} via {used}.", allowed_mentions=ctx.no_pings)
            else:
                owner = managers.assignment(conn, n["handle"])["discord_id"] if esc else None
                msg = await (alerts if esc else ch).send(
                    content=f"<@{owner}> a creator needs your follow-up." if owner else None,
                    embed=nudge_embed(n, c, n["rule_id"]), view=nudge_view(n["id"], escalation=esc),
                    allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=int(owner))]) if owner else ctx.no_pings)
                conn.execute("UPDATE nudges SET review_msg_id=? WHERE id=?", (str(msg.id), n["id"]))
                conn.commit()
        core.set_setting(conn, "nudge:last_run", today.isoformat())
    finally:
        conn.close()
    return f"{len(new)} new check-in{'s' if len(new) != 1 else ''} queued." + (" " + " ".join(notes) if notes else "")


async def act_run_nudges(interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        summary = await run_nudges(force=True)
    except ValueError as e:
        return await ctx.say(interaction, str(e))
    await ctx.say(interaction, summary)


# ---------- weekly report ----------
def report_embed():
    cfg = nudges.load_config()
    g = nudges.goals(cfg)
    conn = ctx.db()
    try:
        r = nudges.weekly_report(conn, cfg)
        act = nudges.agency_active(conn, cfg)
        top = nudges.top_creators(conn, 10)
        mgrs = managers.report(conn, cfg)
        age = nudges.stats_age_days(conn)
    finally:
        conn.close()
    e = discord.Embed(title="Monthly goals report", color=ctx.gold,
                      description=f"Minimums: {g['min_valid_days']} valid LIVE days and {g['min_hours']} hours a month to stay active; "
                                  f"{g['min_diamonds']}+ diamonds for the bonus.")
    if act:
        e.add_field(name="Active creators", value=_active_line(act), inline=False)

    def block(items, fmt):
        return "\n".join(fmt(h, ms) for h, ms in items[:25])[:1000] or "nobody"

    line = lambda h, ms: (f"@{h}: {nudges._fmt(ms['days_done'])}/{ms['days_goal']} days, "
                          f"{nudges._fmt(ms['hours_done'])}/{ms['hours_goal']} hrs")
    e.add_field(name=f"🚨 Can't reach the minimum ({len(r['critical'])})", value=block(r["critical"], line), inline=False)
    e.add_field(name=f"⚠️ Behind pace ({len(r['behind'])})", value=block(r["behind"], line), inline=False)
    e.add_field(name=f"✅ Minimums met ({len(r['met'])})", value=block(r["met"], line), inline=False)
    e.add_field(name=f"💎 Under {g['min_diamonds']} diamonds ({len(r['low_diamonds'])})",
                value=block(r["low_diamonds"], lambda h, ms: f"@{h}: {int(ms['diamonds'])}")
                if r["low_diamonds"] else "nobody", inline=False)
    if mgrs:
        e.add_field(name="👔 Managers", value="\n".join(managers.line(g) for g in mgrs[:10])[:1000], inline=False)
    if top:
        e.add_field(name="🏆 Top 10 by diamonds", value="\n".join(f"{i}. @{t['handle']}: {int(t['diamonds']):,}" for i, t in enumerate(top, 1))[:1000], inline=False)
    if r["no_data"]:
        e.add_field(name=f"No data ({len(r['no_data'])})", value=", ".join(f"@{h}" for h in r["no_data"][:30])[:1000], inline=False)
    e.set_footer(text="Numbers from the last live-stats upload" + (f" ({int(age)} days ago)" if age is not None else " (none yet)"))
    return e


async def act_weekly_report(interaction):
    await ctx.say(interaction, embed=report_embed())


async def post_top10():
    """Community announcement of the week's top 10 (off by default: set announce_top10 to true in nudge_rules.json)."""
    cfg = nudges.load_config()
    if not cfg.get("announce_top10"):
        return
    conn = ctx.db()
    try:
        top = nudges.top_creators(conn, 10)
    finally:
        conn.close()
    if len(top) < cfg.get("announce_top10_min_creators", 10):
        return
    guild = await cs_ctx.creators_guild()
    ch = get_channel(guild, "wins") if guild else None
    if ch is None:
        return
    show = cfg.get("announce_top10_show_diamonds", False)
    lines = [f"{i}. @{t['handle']}" + (f" ({int(t['diamonds']):,} 💎)" if show else "") for i, t in enumerate(top, 1)]
    await ch.send("🏆 **Top 10 creators this month so far**\n" + "\n".join(lines) + "\nKeep going LIVE, everyone!",
                  allowed_mentions=ctx.no_pings)


async def post_weekly_report():
    ch = await staff_channel("reports")
    await ch.send(embed=report_embed(), allowed_mentions=ctx.no_pings)
    await post_top10()
    conn = ctx.db()
    core.set_setting(conn, "report:last_week", nudges.iso_week(datetime.datetime.now(ctx.org_tz).date()))
    conn.close()
