"""Academy in Discord: lesson posts, Mark complete / Take quiz buttons, private one-question-at-a-time quizzes, /progress."""
import discord
from discord import ui

import academy
import creators
from cs_ctx import ctx, creators_guild, get_channel, get_role, ensure_role

LETTERS = "ABCDE"


def lesson_embed(lesson):
    e = discord.Embed(title=lesson["title"], description=lesson["summary"], color=ctx.gold)
    if lesson.get("video_url"):
        e.add_field(name="Watch", value=lesson["video_url"], inline=False)
    if lesson.get("checklist"):
        e.add_field(name="Checklist", value="\n".join(f"☐ {c}" for c in lesson["checklist"])[:1000], inline=False)
    if lesson.get("assignment"):
        e.add_field(name="Assignment", value=lesson["assignment"][:1000], inline=False)
    imgs = lesson.get("images") or []
    if imgs:
        e.set_image(url=imgs[0])
        if len(imgs) > 1:
            e.add_field(name="More images", value="\n".join(imgs[1:])[:1000], inline=False)
    mark = academy.pass_mark(lesson)
    e.set_footer(text=f"Module {lesson['module']}" + (f"  |  Quiz pass mark {mark}%" if lesson.get("quiz") else ""))
    return e


def lesson_view(lesson):
    v = ui.View(timeout=None)
    v.add_item(LessonButton(lesson["id"], "complete"))
    if lesson.get("quiz"):
        v.add_item(LessonButton(lesson["id"], "quiz"))
    return v


def _member_row(conn, user_id):
    c = creators.creator_by_discord(conn, user_id)
    return c if c and c["member_status"] == "member" else None


class LessonButton(ui.DynamicItem[ui.Button], template=r"lesson:(?P<id>[a-z0-9_\-]+):(?P<act>complete|quiz)"):
    def __init__(self, lesson_id, act):
        super().__init__(ui.Button(label="Mark complete" if act == "complete" else "Take quiz",
                                   style=discord.ButtonStyle.success if act == "complete" else discord.ButtonStyle.primary,
                                   custom_id=f"lesson:{lesson_id}:{act}"))
        self.lesson_id, self.act = lesson_id, act

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["id"], match["act"])

    async def callback(self, interaction):
        lessons = academy.load_lessons()
        lesson = lessons.get(self.lesson_id)
        if lesson is None:
            return await ctx.say(interaction, "That lesson isn't available anymore.")
        conn = ctx.db()
        try:
            if not _member_row(conn, interaction.user.id):
                return await ctx.say(interaction, "The academy is for approved creators.")
            if not academy.module_unlocked(conn, interaction.user.id, lessons, lesson["module"]):
                prev = academy.previous_module(lessons, lesson["module"])
                return await ctx.say(interaction, f"Finish **Module {prev}** first. Passing its quizzes unlocks this one.")
            if self.act == "complete":
                academy.mark_complete(conn, interaction.user.id, self.lesson_id)
                msg = "Marked complete ✅"
                if lesson.get("quiz"):
                    msg += "\nNext, tap **Take quiz** to finish this lesson."
                await ctx.say(interaction, msg)
                if not lesson.get("quiz"):
                    await after_progress(interaction, conn, lessons, lesson)
                return
            if not lesson.get("quiz"):
                return await ctx.say(interaction, "This lesson doesn't have a quiz.")
            done = conn.execute("SELECT passed_at FROM lesson_progress WHERE discord_id=? AND lesson_id=?",
                                (str(interaction.user.id), self.lesson_id)).fetchone()
            if done and done["passed_at"]:
                return await ctx.say(interaction, "You've already passed this quiz. 🎉")
            wait = academy.retake_wait(conn, interaction.user.id, self.lesson_id)
            if wait:
                return await ctx.say(interaction, f"You can retake this quiz in about **{wait} minute{'s' if wait != 1 else ''}**. "
                                                  "A little review in the lesson thread can help!")
        finally:
            conn.close()
        view = QuizView(lesson, interaction.user.id)
        await interaction.response.send_message(embed=view.question_embed(), view=view, ephemeral=True)


class QuizView(ui.View):
    """Private to the person taking it. Shows one question at a time with instant feedback."""

    def __init__(self, lesson, user_id):
        super().__init__(timeout=900)
        self.lesson, self.user_id = lesson, user_id
        self.qs = lesson["quiz"]["questions"]
        self.i, self.answers = 0, []
        self._options()

    async def interaction_check(self, interaction):
        return interaction.user.id == self.user_id

    def _options(self):
        self.clear_items()
        q = self.qs[self.i]
        for idx, opt in enumerate(q["options"]):
            b = ui.Button(label=f"{LETTERS[idx]}. {opt}"[:80], style=discord.ButtonStyle.secondary, row=idx // 2 if idx < 4 else 2)
            b.callback = self._make_answer(idx)
            self.add_item(b)

    def question_embed(self):
        q = self.qs[self.i]
        e = discord.Embed(title=f"Question {self.i + 1} of {len(self.qs)}", description=q["q"], color=ctx.gold)
        e.set_footer(text=self.lesson["title"])
        return e

    def _make_answer(self, idx):
        async def answer(interaction):
            q = self.qs[self.i]
            self.answers.append(idx)
            right = idx == q["answer"]
            e = discord.Embed(title=("✅ Correct!" if right else "❌ Not quite."), color=0x2ECC71 if right else 0xE67E22)
            if not right:
                e.description = f"The right answer was **{LETTERS[q['answer']]}. {q['options'][q['answer']]}**"
            if q.get("explanation"):
                e.add_field(name="Why", value=q["explanation"][:1000], inline=False)
            self.clear_items()
            last = self.i + 1 >= len(self.qs)
            nxt = ui.Button(label="See my results" if last else "Next question", style=discord.ButtonStyle.primary)
            nxt.callback = self._finish if last else self._next
            self.add_item(nxt)
            await interaction.response.edit_message(embed=e, view=self)
        return answer

    async def _next(self, interaction):
        self.i += 1
        self._options()
        await interaction.response.edit_message(embed=self.question_embed(), view=self)

    async def _finish(self, interaction):
        pct, passed = academy.grade(self.lesson, self.answers)
        lessons = academy.load_lessons()
        conn = ctx.db()
        try:
            academy.record_attempt(conn, interaction.user.id, self.lesson, pct, passed)
            mark = academy.pass_mark(self.lesson)
            if passed:
                e = discord.Embed(title=f"🎉 You passed with {pct}%!", color=0x2ECC71,
                                  description="Nice work. Your progress has been saved.")
            else:
                e = discord.Embed(title=f"You scored {pct}%", color=0xE67E22,
                                  description=f"The pass mark is {mark}%. Review the lesson and try again in an hour. You've got this!")
            self.stop()
            await interaction.response.edit_message(embed=e, view=None)
            if passed:
                await after_progress(interaction, conn, lessons, self.lesson)
        finally:
            conn.close()


async def after_progress(interaction, conn, lessons, lesson):
    """If that finished the module: give the Module Complete role, unlock the next module, celebrate in #wins (once)."""
    items = academy.by_module(lessons)[lesson["module"]]
    if not academy.module_complete(conn, interaction.user.id, items):
        return
    if not academy.award_module(conn, interaction.user.id, lesson["module"]):
        return
    guild = await creators_guild()
    member = interaction.user if getattr(interaction.user, "guild", None) else None
    if guild is not None:
        try:
            member = member or await guild.fetch_member(interaction.user.id)
            role = await ensure_role(guild, f"Module {lesson['module']} Complete")
            await member.add_roles(role, reason="Passed the module")
            wins = get_channel(guild, "wins")
            if wins:
                await wins.send(f"🎉 {member.mention} just completed **Module {lesson['module']}**! Congratulations!",
                                allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException as e:
            print(f"Couldn't finish the module award: {e}")
    nxt = [m for m in academy.by_module(lessons) if m > lesson["module"]]
    msg = f"🏅 **Module {lesson['module']} complete!**" + (f" Module {nxt[0]} is now unlocked." if nxt else " You've finished everything available so far.")
    await interaction.followup.send(msg, ephemeral=True)


def progress_embed(conn, user_id):
    lessons = academy.load_lessons()
    e = discord.Embed(title="Your academy progress", color=ctx.gold)
    for m in academy.progress(conn, user_id, lessons):
        status = "✅ Complete" if m["complete"] else ("🔓 In progress" if m["unlocked"] else "🔒 Locked")
        lines = [f"{'✅' if done else '⬜'} {l['title']}" for l, done in m["lessons"]]
        e.add_field(name=f"Module {m['module']}: {status}", value="\n".join(lines)[:1000], inline=False)
    if not lessons:
        e.description = "No lessons have been published yet."
    try:
        import nudges, unlock
        cfg = nudges.load_config()
        cr = creators.creator_by_discord(conn, user_id)
        line = unlock.progress_text(conn, cr["handle"], cfg) if cr else None
        if line:
            e.insert_field_at(0, name="🔒 Unlock the full community", value=line, inline=False)
    except Exception:
        pass
    return e


async def publish_lesson(guild, lesson_id):
    """Post a lesson into #lessons (a forum post, or a thread if the channel is a plain text channel). Re-publishing edits it."""
    lessons = academy.load_lessons()
    lesson = lessons.get(lesson_id)
    if lesson is None:
        raise ValueError("I couldn't find that lesson file.")
    ch = get_channel(guild, "lessons")
    if ch is None:
        raise ValueError("There's no #lessons channel yet. Run /setup-server first.")
    embed, view = lesson_embed(lesson), lesson_view(lesson)
    conn = ctx.db()
    try:
        rec = academy.post_record(conn, lesson_id)
        if rec:
            try:
                thread = guild.get_thread(int(rec["thread_id"])) or await guild.fetch_channel(int(rec["thread_id"]))
                msg = await thread.fetch_message(int(rec["message_id"]))
                await msg.edit(embed=embed, view=view)
                return thread, True
            except discord.HTTPException:
                pass
        name = f"Module {lesson['module']}: {lesson['title']}"[:100]
        if isinstance(ch, discord.ForumChannel):
            made = await ch.create_thread(name=name, embed=embed, view=view)
            thread, msg = made.thread, made.message
        else:
            msg = await ch.send(embed=embed, view=view)
            thread = await msg.create_thread(name=name)
        academy.save_post(conn, lesson_id, ch.id, thread.id, msg.id)
        return thread, False
    finally:
        conn.close()
