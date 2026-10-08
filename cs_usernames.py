"""Username changes: staff commands, the creator's /update-username request, and the approval card."""
import discord
from discord import app_commands, ui

import core
import usernames
from cs_apply import contact_creator
from cs_ctx import ctx, staff_channel, admin_check

def _pn(platform):
    return {"tiktok": "TikTok", "instagram": "Instagram"}.get(platform, str(platform).title())


PLATFORMS = [app_commands.Choice(name="TikTok", value="tiktok"), app_commands.Choice(name="Instagram", value="instagram")]


def request_embed(req, creator, status=None):
    e = discord.Embed(title="Username change request", color=ctx.gold,
                      description=f"<@{req['discord_id']}> wants to change their **{_pn(req['platform'])}** username.")
    e.add_field(name="From", value=f"@{req['old_name']}")
    e.add_field(name="To", value=f"@{req['new_name']}")
    if creator is not None:
        e.add_field(name="TikTok", value=f"@{creator['handle']}")
    if status:
        e.add_field(name="Result", value=status, inline=False)
    e.set_footer(text=f"Request #{req['id']}  |  Check the new name is really theirs before approving")
    return e


def request_view(rid, current=None):
    v = ui.View(timeout=None)
    for act in ("ok", "no"):
        v.add_item(RequestButton(rid, act, current))
    return v


class RequestButton(ui.DynamicItem[ui.Button], template=r"uname:(?P<id>\d+):(?P<act>ok|no)"):
    def __init__(self, rid, act, current=None):
        super().__init__(ui.Button(label="Approve" if act == "ok" else "Deny",
                                   style=discord.ButtonStyle.success if act == "ok" else discord.ButtonStyle.danger,
                                   custom_id=f"uname:{rid}:{act}", disabled=current is not None))
        self.rid, self.act = rid, act

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
            try:
                req = usernames.decide_request(conn, self.rid, self.act == "ok", interaction.user.id)
            except ValueError as e:
                return await ctx.say(interaction, str(e))
            cr = conn.execute("SELECT * FROM creators WHERE discord_id=?", (req["discord_id"],)).fetchone()
        finally:
            conn.close()
        ok = req["status"] == "approved"
        result = f"{'Approved' if ok else 'Denied'} by <@{interaction.user.id}>"
        await interaction.edit_original_response(embed=request_embed(req, cr, result), view=request_view(self.rid, self.act))
        if cr is not None:
            text = (f"Your {_pn(req['platform'])} username is now @{req['new_name']} in the Hierarchy Music system. - Hierarchy Music"
                    if ok else
                    f"We couldn't change your {_pn(req['platform'])} username to @{req['new_name']} right now. "
                    "Message us in #support and we'll sort it out. - Hierarchy Music")
            try:
                await contact_creator(cr, text, "account", "Your username change")
            except Exception as ex:
                print(f"[creators] username notice failed: {ex}", flush=True)


def history_embed(conn, handle):
    h = core.norm_handle(handle)
    cr = conn.execute("SELECT * FROM creators WHERE handle=?", (h,)).fetchone()
    cur = usernames.current_handle(conn, h)
    if cr is None and cur != h:
        cr = conn.execute("SELECT * FROM creators WHERE handle=?", (cur,)).fetchone()
        h = cur
    hist = usernames.previous_names(conn, h)
    e = discord.Embed(title=f"Username history: @{h}", color=ctx.gold)
    if cr is not None:
        e.add_field(name="TikTok now", value=f"@{cr['handle']}")
        e.add_field(name="Instagram now", value=f"@{cr['ig_username']}" if cr["ig_username"] else "none on file")
    if not hist:
        e.description = "No username changes on record."
    else:
        e.description = "\n".join(f"{_pn(p)}: @{o} → @{n}  ({str(w)[:10]}, {src or 'staff'})" for p, o, n, w, src in hist)[:3500]
    return e


PANEL_TITLE = "Changed your username?"
PANEL_TEXT = ("Changed your TikTok or Instagram username? Tap a button below, type your new username, and you're done. "
              "We'll check it and update your account. You'll get a message when it's changed.\n\n"
              "Your old username stays on file, so your stats and history stay connected. "
              "Nothing about your account is lost.\n\n*Only for approved members. Not a member yet? Apply in #apply.*")


def panel_embed():
    return discord.Embed(title=PANEL_TITLE, description=PANEL_TEXT, color=ctx.gold)


async def submit_request(interaction, platform, new_name):
    """Shared by the buttons and the /update-username command. The creator only ever sees plain, friendly messages."""
    conn = ctx.db()
    try:
        try:
            req = usernames.new_request(conn, interaction.user.id, platform, new_name)
        except ValueError as e:
            return await ctx.say(interaction, str(e))
        cr = usernames.creator_by_discord(conn, interaction.user.id)
        ch = await staff_channel("approvals")
        await ch.send(embed=request_embed(req, cr), view=request_view(req["id"]), allowed_mentions=ctx.no_pings)
    finally:
        conn.close()
    await ctx.say(interaction, f"Thanks! We got your new {_pn(platform)} username (@{req['new_name']}). "
                               "We'll check it and update your account, usually within a day. You'll get a message when it's done.")


class UsernameModal(ui.Modal):
    def __init__(self, platform):
        super().__init__(title=f"New {_pn(platform)} username")
        self.platform = platform
        self.new_name = ui.TextInput(label=f"Your new {_pn(platform)} username", placeholder="@yournewname",
                                     min_length=2, max_length=60)
        self.add_item(self.new_name)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await submit_request(interaction, self.platform, self.new_name.value)


class UsernamePanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Change my TikTok username", style=discord.ButtonStyle.primary, custom_id="uname:panel:tiktok")
    async def tiktok_btn(self, interaction, button):
        await interaction.response.send_modal(UsernameModal("tiktok"))

    @ui.button(label="Change my Instagram username", style=discord.ButtonStyle.secondary, custom_id="uname:panel:instagram")
    async def ig_btn(self, interaction, button):
        await interaction.response.send_modal(UsernameModal("instagram"))


def register(bot, gobj):
    @bot.tree.command(name="rename-creator", description="Change a creator's username everywhere and keep the old one on record")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(platform="Which username changed", handle="Their CURRENT TikTok username in our system", new_username="The new username")
    @app_commands.choices(platform=PLATFORMS)
    async def rename_creator(interaction: discord.Interaction, platform: app_commands.Choice[str], handle: str, new_username: str):
        conn = ctx.db()
        try:
            try:
                if platform.value == "tiktok":
                    new = usernames.rename_tiktok(conn, handle, new_username, by=interaction.user.id, source="staff command")
                else:
                    new = usernames.rename_instagram(conn, usernames.current_handle(conn, handle), new_username,
                                                     by=interaction.user.id, source="staff command")
            except ValueError as e:
                return await ctx.say(interaction, str(e))
        finally:
            conn.close()
        await ctx.say(interaction, f"Done. {platform.name} username is now @{new}. The old one is kept in their history.")

    @bot.tree.command(name="creator-history", description="See a creator's previous usernames")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(handle="Any username they have used (TikTok)")
    async def creator_history(interaction: discord.Interaction, handle: str):
        conn = ctx.db()
        try:
            await ctx.say(interaction, embed=history_embed(conn, handle))
        finally:
            conn.close()

    if gobj is None:
        return

    @bot.tree.command(name="update-username", description="Tell us your TikTok or Instagram username changed", guild=gobj)
    @app_commands.describe(platform="Which username changed", new_username="Your new username")
    @app_commands.choices(platform=PLATFORMS)
    async def update_username(interaction: discord.Interaction, platform: app_commands.Choice[str], new_username: str):
        await interaction.response.defer(ephemeral=True)
        await submit_request(interaction, platform.value, new_username)
