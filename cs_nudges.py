"""Staff side of the creators server: roster and live-stats uploads, the nudge approval queue, the weekly report."""
import datetime

import discord
from discord import ui

import core
import creators
import nudges
from cs_apply import contact_creator
from cs_ctx import ctx, staff_channel, admin_check


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
        n = nudges.save_stats(conn, rows)
    except ValueError as e:
        return await ctx.say(interaction, str(e))
    finally:
        conn.close()
    missing = [k.replace("_", " ") for k in cfg["columns"] if k not in found]
    e = discord.Embed(title="Live stats saved", color=ctx.gold,
                      description=f"{n} creators in the file. Nudges use these numbers the next time they run (or tap **Run nudges**).")
    e.add_field(name="Columns found", value=", ".join(k.replace("_", " ") for k in found) or "none", inline=False)
    if missing:
        e.add_field(name="Not in this file", value=", ".join(missing) + "\nRules that need those columns will be skipped.", inline=False)
    await ctx.say(interaction, embed=e)


# ---------- the approval queue ----------
def nudge_embed(n, creator, rule_id, status=None):
    e = discord.Embed(title=f"Check-in for @{n['handle']}", description=n["text"], color=ctx.gold)
    e.add_field(name="Rule", value=rule_id.replace("_", " "))
    if creator is not None:
        e.add_field(name="Name", value=creator["full_name"] or "—")
    if status:
        e.add_field(name="Result", value=status, inline=False)
    e.set_footer(text=f"Nudge #{n['id']}  |  Nothing is sent until you tap Send")
    return e


def nudge_view(nid, current=None):
    v = ui.View(timeout=None)
    for act in ("send", "skip"):
        v.add_item(NudgeButton(nid, act, current))
    return v


class NudgeButton(ui.DynamicItem[ui.Button], template=r"nudge:(?P<id>\d+):(?P<act>send|skip)"):
    def __init__(self, nid, act, current=None):
        super().__init__(ui.Button(label="Send" if act == "send" else "Skip",
                                   style=discord.ButtonStyle.success if act == "send" else discord.ButtonStyle.secondary,
                                   custom_id=f"nudge:{nid}:{act}", disabled=current is not None))
        self.nid, self.act = nid, act

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["id"]), match["act"])

    async def interaction_check(self, interaction):
        if not admin_check(interaction):
            await ctx.say(interaction, "Admins only.")
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
            else:
                if c is None or c["member_status"] != "member":
                    return await ctx.say(interaction, "That creator isn't an active member anymore.")
                used = await contact_creator(c, n["text"], "checkin", "A check-in from Hierarchy Music")
                nudges.mark_nudge(conn, self.nid, "sent", channel=used, decided_by=str(interaction.user.id))
                result = f"Sent by <@{interaction.user.id}> via **{used}**"
            n = nudges.get_nudge(conn, self.nid)
        finally:
            conn.close()
        await interaction.edit_original_response(embed=nudge_embed(n, c, n["rule_id"], result), view=nudge_view(self.nid, self.act))


async def run_nudges(force=False):
    """Build today's nudges and put them in the staff queue (or send them if auto_send is on). Returns a text summary."""
    cfg = nudges.load_config()
    today = datetime.datetime.now(ctx.org_tz).date()
    conn = ctx.db()
    try:
        new, notes = nudges.build_nudges(conn, cfg, today)
        ch = await staff_channel()
        for note in notes:
            await ch.send(f"ℹ️ Check-ins: {note}", allowed_mentions=ctx.no_pings)
        for n in new:
            c = conn.execute("SELECT * FROM creators WHERE handle=?", (n["handle"],)).fetchone()
            if cfg.get("auto_send"):
                used = await contact_creator(c, n["text"], "checkin", "A check-in from Hierarchy Music")
                nudges.mark_nudge(conn, n["id"], "sent", channel=used, decided_by="auto")
                await ch.send(f"Auto-sent a check-in to @{n['handle']} via {used}.", allowed_mentions=ctx.no_pings)
            else:
                msg = await ch.send(embed=nudge_embed(n, c, n["rule_id"]), view=nudge_view(n["id"]), allowed_mentions=ctx.no_pings)
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
    conn = ctx.db()
    try:
        r = nudges.weekly_report(conn, cfg)
        age = nudges.stats_age_days(conn)
    finally:
        conn.close()
    e = discord.Embed(title="Weekly check-in report", color=ctx.gold)

    def block(items):
        return "\n".join(f"@{h}: {nudges._fmt(done)} of {nudges._fmt(goal)} hrs ({pct}%)" for h, done, goal, pct in items[:25])[:1000] or "nobody"

    e.add_field(name=f"🎯 Hit their goal ({len(r['hit'])})", value=block(r["hit"]), inline=False)
    e.add_field(name=f"👍 Close ({len(r['close'])})", value=block(r["close"]), inline=False)
    e.add_field(name=f"🤝 Behind ({len(r['behind'])})", value=block(r["behind"]), inline=False)
    if r["no_data"]:
        e.add_field(name=f"No data this week ({len(r['no_data'])})", value=", ".join(f"@{h}" for h in r["no_data"][:30])[:1000], inline=False)
    e.set_footer(text="Numbers from the last live-stats upload" + (f" ({int(age)} days ago)" if age is not None else " (none yet)"))
    return e


async def act_weekly_report(interaction):
    await ctx.say(interaction, embed=report_embed())


async def post_weekly_report():
    ch = await staff_channel()
    await ch.send(embed=report_embed(), allowed_mentions=ctx.no_pings)
    conn = ctx.db()
    core.set_setting(conn, "report:last_week", nudges.iso_week(datetime.datetime.now(ctx.org_tz).date()))
    conn.close()
