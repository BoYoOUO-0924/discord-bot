"""專案（專案管理第二階段）：把待辦分成專案與里程碑、每日摘要、每週 AI 週報。

- 每個專案綁一個頻道：在那個頻道 /task add 的待辦預設屬於這個專案，摘要和週報也發在那裡
- 每天早上 9 點：各專案「逾期、今天到期、進行中」的摘要（沒有東西就不發）
- 每週五下午 5 點：週報（本週完成、逾期、卡住、下週到期、里程碑進度），由 AI 寫一段總結；
  AI 不能用時改用數字寫的摘要，週報照樣會發
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import tasks as tk
from core.ai import AIError
from core.timeutil import now_tw, ts
from core.ui import Color, defer, make_embed, respond, send_error, success_embed

log = logging.getLogger(__name__)

DIGEST_KEY = "task_digest_day"
REPORT_KEY = "task_report_week"
FIELD_LIMIT = 1024
NO_MENTIONS = discord.AllowedMentions.none()


def task_line(task, now: Optional[float] = None, show_due: bool = True) -> str:
    who = f"<@{task['assignee_id']}>" if task["assignee_id"] else "沒人負責"
    due = ""
    if show_due and task["due_at"] is not None:
        late = task["status"] != "done" and task["due_at"] <= (now or time.time())
        due = f"・{'⚠️ ' if late else ''}{ts(task['due_at'], 'R')}截止"
    return f"`#{task['id']}` {task['title'][:50]}・{who}{due}"


def join_lines(lines: List[str], limit: int = FIELD_LIMIT) -> str:
    """把多行塞進一個欄位，超過長度就寫「還有 N 個」。"""
    out, used = [], 0
    for i, line in enumerate(lines):
        more = f"…還有 {len(lines) - i} 個"
        if used + len(line) + 1 > limit - len(more) - 1:
            out.append(more)
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out) or "（沒有）"


def can_manage(project, user) -> bool:
    return user.id == project["creator_id"] or (isinstance(user, discord.Member)
                                               and user.guild_permissions.manage_messages)


class Projects(commands.Cog):
    project_group = app_commands.Group(name="project", description="專案：把待辦分組、里程碑、每日摘要與週報",
                                       guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.schedule.start()

    async def cog_unload(self):
        self.schedule.cancel()

    @property
    def tasks_cog(self):
        return self.bot.get_cog("Tasks")

    async def find(self, interaction: discord.Interaction, project_id: int):
        project = await self.bot.db.get_project(project_id)
        if not project or project["guild_id"] != interaction.guild_id:
            await send_error(interaction, "找不到這個專案，請從選單選擇。")
            return None
        return project

    async def project_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.tasks_cog.project_choices(interaction, current)

    # ------------------------------------------------------------------ 建立 / 清單
    @project_group.command(name="create", description="建立專案，綁定一個頻道（摘要和週報會發在那裡）")
    @app_commands.describe(name="專案名稱", channel="專案頻道（預設是目前的頻道）", description="專案說明")
    async def create(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 50],
                     channel: Optional[discord.TextChannel] = None,
                     description: Optional[app_commands.Range[str, 1, 300]] = None):
        if await self.bot.db.count_projects(interaction.guild_id) >= tk.MAX_PROJECTS_PER_GUILD:
            return await send_error(interaction, f"一個伺服器最多 {tk.MAX_PROJECTS_PER_GUILD} 個專案，先封存一些吧。")
        channel_id = channel.id if channel else interaction.channel_id
        project_id = await self.bot.db.create_project(interaction.guild_id, name.strip(), channel_id,
                                                      interaction.user.id, (description or "").strip())
        if not project_id:
            return await send_error(interaction, f"已經有叫「{name}」的專案了（封存的也算），換個名字吧。")
        embed = success_embed(f"已建立專案 **📁 {name}**，綁定 <#{channel_id}>。")
        embed.add_field(name="接下來", value=(
            f"・在 <#{channel_id}> 用 `/task add` 新增的待辦，會自動歸到這個專案\n"
            "・`/project milestone` 新增里程碑，`/project view` 看進度\n"
            f"・每天早上 {tk.DIGEST_HOUR} 點發今日摘要，每週五 {tk.REPORT_HOUR} 點發週報"), inline=False)
        await respond(interaction, embed=embed)
        log.info("專案 #%d 建立：%s", project_id, name)

    @project_group.command(name="list", description="列出伺服器的專案與進度")
    @app_commands.describe(include_archived="也列出已封存的專案")
    async def list_command(self, interaction: discord.Interaction, include_archived: bool = False):
        await defer(interaction, ephemeral=True)
        rows = await self.bot.db.list_projects(interaction.guild_id, include_archived)
        if not rows:
            return await respond(interaction, ephemeral=True, embed=make_embed(
                "📁 專案", "還沒有任何專案。用 `/project create` 建立第一個吧！", Color.MUTED))
        lines = []
        for p in rows:
            counts = await self.bot.db.project_counts(p["id"])
            total = counts["todo"] + counts["doing"] + counts["done"]
            late = f"・⚠️ 逾期 {counts['overdue']}" if counts["overdue"] else ""
            archived = "（已封存）" if p["archived"] else ""
            lines.append(f"**📁 {p['name']}**{archived}・<#{p['channel_id']}>\n"
                         f"　{tk.fmt_progress(counts['done'], total)}・進行中 {counts['doing']}{late}")
        await respond(interaction, ephemeral=True, embed=make_embed("📁 專案", "\n".join(lines)[:4000]))

    # ------------------------------------------------------------------ 查看
    async def view_embed(self, project) -> discord.Embed:
        counts = await self.bot.db.project_counts(project["id"])
        total = counts["todo"] + counts["doing"] + counts["done"]
        title = f"📁 {project['name']}" + ("（已封存）" if project["archived"] else "")
        embed = make_embed(title, project["description"] or None, Color.MUTED if project["archived"] else Color.PRIMARY)
        embed.add_field(name="整體進度", value=tk.fmt_progress(counts["done"], total), inline=False)
        embed.add_field(name="待辦", value=str(counts["todo"]))
        embed.add_field(name="進行中", value=str(counts["doing"]))
        embed.add_field(name="逾期", value=f"⚠️ {counts['overdue']}" if counts["overdue"] else "0")
        milestones = await self.bot.db.list_milestones(project["id"])
        if milestones:
            lines = []
            for m in milestones:
                due = f"・{ts(m['due_at'], 'R')}" if m["due_at"] else ""
                lines.append(f"🏁 **{m['name']}**{due}\n　{tk.fmt_progress(m['done'], m['total'])}")
            embed.add_field(name="里程碑", value=join_lines(lines), inline=False)
        open_tasks = await self.bot.db.list_tasks(project["guild_id"], project_id=project["id"], limit=10)
        if open_tasks:
            now = time.time()
            embed.add_field(name="未完成（依截止時間）",
                            value=join_lines([f"{tk.STATUS_EMOJI[t['status']]} {task_line(t, now)}" for t in open_tasks]),
                            inline=False)
        embed.set_footer(text="/task list 可以只看這個專案；/project report 立刻產生週報")
        return embed

    @project_group.command(name="view", description="查看專案進度、里程碑與未完成的待辦")
    @app_commands.describe(project="要看的專案", public="公開發在頻道裡")
    async def view(self, interaction: discord.Interaction, project: int, public: bool = False):
        row = await self.find(interaction, project)
        if row:
            await defer(interaction, ephemeral=not public)
            await respond(interaction, embed=await self.view_embed(row), ephemeral=not public)

    # ------------------------------------------------------------------ 里程碑
    @project_group.command(name="milestone", description="在專案裡新增里程碑")
    @app_commands.describe(project="哪個專案", name="里程碑名稱，例如 第一版上線", due="目標日期，例如 11/30、下週五")
    async def milestone(self, interaction: discord.Interaction, project: int, name: app_commands.Range[str, 1, 50],
                        due: Optional[str] = None):
        row = await self.find(interaction, project)
        if not row:
            return
        due_at = None
        if due:
            parsed = tk.parse_due(due, now_tw())
            if not parsed:
                return await send_error(interaction, f"看不懂「{due}」這個日期，或是已經過了。")
            due_at = parsed.at.timestamp()
        milestone_id = await self.bot.db.create_milestone(project, name.strip(), due_at)
        if not milestone_id:
            return await send_error(interaction, f"專案裡已經有叫「{name}」的里程碑了。")
        due_text = f"，目標 {ts(due_at, 'D')}" if due_at else ""
        await respond(interaction, embed=success_embed(
            f"已在 📁 {row['name']} 新增里程碑 **🏁 {name}**{due_text}。\n"
            "新增待辦時在 `milestone` 選它，進度就會算進來。"))

    # ------------------------------------------------------------------ 封存
    @project_group.command(name="archive", description="封存或恢復專案（封存後不再發摘要和週報，待辦保留）")
    @app_commands.describe(project="哪個專案")
    async def archive(self, interaction: discord.Interaction, project: int):
        row = await self.find(interaction, project)
        if not row:
            return
        if not can_manage(row, interaction.user):
            return await send_error(interaction, "只有建立專案的人或管理員可以封存。")
        archived = not row["archived"]
        await self.bot.db.update_project(project, archived=int(archived))
        verb = "封存" if archived else "恢復"
        await respond(interaction, embed=success_embed(f"已{verb}專案 📁 {row['name']}。"))

    # ------------------------------------------------------------------ 週報
    async def names(self, user_ids) -> Dict[int, str]:
        ids = [uid for uid in set(user_ids) if uid]
        found = await self.bot.db.get_names(ids) if ids else {}
        return {uid: found[uid][0] for uid in found}

    async def build_report(self, project, now: Optional[datetime] = None):
        """回傳 (WeekReport, 週報嵌入訊息, 這週有沒有任何東西可以報)。"""
        now = now or now_tw()
        db, pid = self.bot.db, project["id"]
        start = tk.week_start(now)
        end = start + timedelta(days=7)
        done = await db.project_tasks_between(pid, "done_at", start.timestamp(), end.timestamp())
        created = await db.project_tasks_between(pid, "created_at", start.timestamp(), end.timestamp())
        next_week = await db.project_tasks_between(pid, "due_at", end.timestamp(), (end + timedelta(days=7)).timestamp())
        open_tasks = await db.list_tasks(project["guild_id"], project_id=pid, limit=500)
        overdue = [t for t in open_tasks if t["due_at"] is not None and t["due_at"] <= now.timestamp()]
        stuck = await db.project_stuck_tasks(pid, (now - timedelta(days=tk.STUCK_DAYS)).timestamp())
        milestones = await db.list_milestones(pid)

        names = await self.names(t["assignee_id"] for t in done + overdue + stuck + next_week)
        label = lambda t: t["title"] + (f"（{names[t['assignee_id']]}）" if t["assignee_id"] in names else "")
        report = tk.WeekReport(
            project=project["name"], done=[label(t) for t in done], created=len(created),
            overdue=[label(t) for t in overdue], stuck=[label(t) for t in stuck], next_week=[label(t) for t in next_week],
            open_count=len(open_tasks), milestones=[(m["name"], m["done"], m["total"]) for m in milestones])
        has_content = bool(done or created or open_tasks)

        summary = await self.summarize(report)
        week = f"{start:%m/%d}–{end - timedelta(days=1):%m/%d}"
        embed = make_embed(f"📊 {project['name']} 週報（{week}）", summary, Color.GOLD)
        stamp = now.timestamp()
        embed.add_field(name=f"✅ 本週完成（{len(done)}）", value=join_lines([task_line(t, stamp, False) for t in done]),
                        inline=False)
        if overdue:
            embed.add_field(name=f"⚠️ 逾期（{len(overdue)}）", value=join_lines([task_line(t, stamp) for t in overdue]),
                            inline=False)
        if stuck:
            embed.add_field(name=f"🐢 進行中但超過 {tk.STUCK_DAYS} 天沒更新（{len(stuck)}）",
                            value=join_lines([task_line(t, stamp) for t in stuck]), inline=False)
        embed.add_field(name=f"📅 下週到期（{len(next_week)}）",
                        value=join_lines([task_line(t, stamp) for t in next_week]), inline=False)
        if milestones:
            embed.add_field(name="🏁 里程碑", value=join_lines(
                [f"**{m['name']}** {tk.fmt_progress(m['done'], m['total'])}" for m in milestones]), inline=False)
        embed.set_footer(text=f"本週新增 {len(created)} 個・目前未完成 {len(open_tasks)} 個")
        return report, embed, has_content

    async def summarize(self, report: tk.WeekReport) -> str:
        ai = self.bot.ai
        if ai.available:
            try:
                text = await ai.text(tk.report_prompt(report), temperature=0.3, max_tokens=500)
                text = text.strip().replace("@everyone", "everyone").replace("@here", "here")
                if text:
                    return text[:1500]
            except AIError as e:
                log.info("週報 AI 摘要失敗（%s），改用數字摘要", e)
        return tk.fallback_summary(report) + "\n-# AI 目前無法使用，這段是用數字自動產生的。"

    @project_group.command(name="report", description="立刻產生這個專案本週的週報")
    @app_commands.describe(project="哪個專案")
    async def report(self, interaction: discord.Interaction, project: int):
        row = await self.find(interaction, project)
        if not row:
            return
        await defer(interaction)   # AI 寫摘要可能要十幾秒
        _, embed, _ = await self.build_report(row)
        await respond(interaction, embed=embed, allowed_mentions=NO_MENTIONS)

    # ------------------------------------------------------------------ 每日摘要
    async def build_digest(self, project, now: Optional[datetime] = None) -> Optional[discord.Embed]:
        """今日摘要；沒有逾期、今天到期、進行中的待辦就回傳 None（不發）。"""
        now = now or now_tw()
        stamp = now.timestamp()
        end_of_day = (tk.day_start(now) + timedelta(days=1)).timestamp()
        open_tasks = await self.bot.db.list_tasks(project["guild_id"], project_id=project["id"], limit=500)
        overdue = [t for t in open_tasks if t["due_at"] is not None and t["due_at"] <= stamp]
        today = [t for t in open_tasks if t["due_at"] is not None and stamp < t["due_at"] < end_of_day]
        listed = {t["id"] for t in overdue + today}
        doing = [t for t in open_tasks if t["status"] == "doing" and t["id"] not in listed]
        if not (overdue or today or doing):
            return None
        weekday = "一二三四五六日"[now.weekday()]
        embed = make_embed(f"☀️ {project['name']}・今日待辦 {now:%m/%d}（{weekday}）",
                           f"未完成共 {len(open_tasks)} 個", Color.WARNING if overdue else Color.PRIMARY)
        if overdue:
            embed.add_field(name=f"⚠️ 已逾期（{len(overdue)}）", value=join_lines([task_line(t, stamp) for t in overdue]),
                            inline=False)
        if today:
            embed.add_field(name=f"📅 今天到期（{len(today)}）", value=join_lines([task_line(t, stamp) for t in today]),
                            inline=False)
        if doing:
            embed.add_field(name=f"🔨 進行中（{len(doing)}）", value=join_lines([task_line(t, stamp) for t in doing]),
                            inline=False)
        embed.set_footer(text="/task list 看全部待辦・/project view 看專案進度")
        return embed

    # ------------------------------------------------------------------ 排程
    @tasks.loop(minutes=1)
    async def schedule(self):
        now = now_tw()
        if tk.digest_due(now, await self.bot.db.get_meta(DIGEST_KEY)):
            # 先記下「今天發過了」，就算發到一半出錯，也不會一直重發
            await self.bot.db.set_meta(DIGEST_KEY, now.date().isoformat())
            await self.send_all("每日摘要", self.build_digest)
        if tk.report_due(now, await self.bot.db.get_meta(REPORT_KEY)):
            await self.bot.db.set_meta(REPORT_KEY, tk.week_key(now))

            async def weekly(project):
                _, embed, has_content = await self.build_report(project)
                return embed if has_content else None
            await self.send_all("週報", weekly)

    @schedule.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def send_all(self, what: str, build) -> None:
        sent = 0
        for project in await self.bot.db.list_projects():
            try:
                embed = await build(project)
                if embed is None:
                    continue
                channel = self.bot.get_channel(project["channel_id"]) or await self.bot.fetch_channel(project["channel_id"])
                await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
                sent += 1
            except (discord.NotFound, discord.Forbidden):
                log.info("專案「%s」的頻道不存在或沒有權限，略過%s", project["name"], what)
            except Exception:
                log.exception("專案「%s」的%s發送失敗", project["name"], what)
        if sent:
            log.info("已發送 %d 個專案的%s", sent, what)


# 自動完成：專案參數全部共用同一個來源
for _command in (Projects.view, Projects.milestone, Projects.archive, Projects.report):
    _command.autocomplete("project")(Projects.project_autocomplete)


async def setup(bot):
    await bot.add_cog(Projects(bot))
