"""待辦（專案管理第一階段）：任務卡片、負責人、截止日、到期提醒。

- /task add 建立任務，在頻道發一張卡片；卡片上的按鈕切換「待辦 → 進行中 → 完成」，bot 重啟後仍然有效
- /task list 查看伺服器的待辦，/task done 直接完成某個任務
- 截止前 24 小時提醒一次，逾期再通知一次，都發在卡片所在的頻道並提及負責人
- 權限：建立者、負責人、有「管理訊息」權限的人可以改；沒人負責的任務任何人都能認領
"""
import logging
import time
from datetime import datetime
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import tasks as tk
from core.timeutil import TAIPEI, now_tw, ts
from core.ui import BaseView, Color, defer, fmt_points, make_embed, respond, send_error, success_embed

log = logging.getLogger(__name__)

MAX_OPEN_PER_GUILD = 200
LIST_LIMIT = 25
DUE_PRESETS = ["明天", "後天", "週五", "下週一", "明天 18:00", "3d"]
DUE_HELP = "支援的格式：`明天`、`週五`、`下週一`、`11/20`、`明天 18:00`、`週五下午3點`、`3d`"
SCOPES = {"all": "全部", "mine": "我的", "assigned": "指派給我的", "unassigned": "沒人負責的"}
ALLOWED = discord.AllowedMentions(users=True, everyone=False, roles=False)


def is_mod(user) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.manage_messages


def can_edit(task, user) -> bool:
    return user.id in (task["creator_id"], task["assignee_id"]) or is_mod(user)


def can_delete(task, user) -> bool:
    return user.id == task["creator_id"] or is_mod(user)


def is_overdue(task, now: Optional[float] = None) -> bool:
    return task["status"] != "done" and task["due_at"] is not None and task["due_at"] <= (now or time.time())


def due_text(task) -> str:
    if task["due_at"] is None:
        return "沒有截止日"
    if task["due_has_time"]:
        return f"{ts(task['due_at'], 'f')}（{ts(task['due_at'], 'R')}）"
    return f"{tk.fmt_due(task['due_at'], False)} 當天（{ts(task['due_at'], 'R')}）"


def owner_of(task) -> int:
    """通知要提及誰：有負責人找負責人，沒有就找建立者。"""
    return task["assignee_id"] or task["creator_id"]


def build_embed(task, project: Optional[str] = None, milestone: Optional[str] = None) -> discord.Embed:
    status = task["status"]
    overdue = is_overdue(task)
    color = {"todo": Color.PRIMARY, "doing": Color.WARNING, "done": Color.SUCCESS}[status]
    if overdue:
        color = Color.DANGER
    title = f"{tk.STATUS_EMOJI[status]} #{task['id']} {task['title']}"
    embed = make_embed(title[:256], task["note"] or None, color)
    state = tk.STATUS_LABELS[status] + ("・⚠️ 已逾期" if overdue else "")
    embed.add_field(name="狀態", value=state)
    embed.add_field(name="負責人", value=f"<@{task['assignee_id']}>" if task["assignee_id"] else "還沒有人（可以認領）")
    embed.add_field(name="優先度", value=f"{tk.PRIORITY_EMOJI[task['priority']]} {tk.PRIORITIES[task['priority']]}")
    if project:
        embed.add_field(name="專案", value=f"📁 {project}" + (f"・🏁 {milestone}" if milestone else ""), inline=False)
    embed.add_field(name="截止", value=due_text(task), inline=False)
    if task["repeat"] and status != "done":
        embed.add_field(name="重複", value=f"🔁 {tk.REPEATS.get(task['repeat'], task['repeat'])}（完成後自動建立下一次）",
                        inline=False)
    if status == "done" and task["done_at"]:
        by = f"<@{task['done_by']}>" if task["done_by"] else "管理後台或 GitHub"
        embed.add_field(name="完成", value=f"{by}・{ts(task['done_at'], 'R')}", inline=False)
    embed.add_field(name="建立者", value=f"<@{task['creator_id']}>・{ts(task['created_at'], 'R')}", inline=False)
    embed.set_footer(text="用下面的按鈕更新進度；/task list 看全部待辦")
    return embed


class TaskButton(discord.ui.DynamicItem[discord.ui.Button], template=r"task:(?P<task_id>\d+):(?P<action>[a-z]+)"):
    def __init__(self, task_id: int, action: str, *, label: Optional[str] = None, emoji: Optional[str] = None,
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary, row: Optional[int] = None):
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style, row=row,
                                           custom_id=f"task:{task_id}:{action}"))
        self.task_id, self.action = task_id, action

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["task_id"]), match["action"])

    async def callback(self, interaction: discord.Interaction):
        cog: Tasks = interaction.client.get_cog("Tasks")
        try:
            await cog.handle_button(interaction, self.task_id, self.action)
        except Exception:
            log.exception("待辦按鈕發生錯誤（#%s %s）", self.task_id, self.action)
            await send_error(interaction, "操作時發生錯誤，請稍後再試。")


def build_view(task) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    tid, status = task["id"], task["status"]
    if status == "todo":
        view.add_item(TaskButton(tid, "doing", label="開始", emoji="🔨", style=discord.ButtonStyle.primary, row=0))
    if status != "done":
        view.add_item(TaskButton(tid, "done", label="完成", emoji="✅", style=discord.ButtonStyle.success, row=0))
        if not task["assignee_id"]:
            view.add_item(TaskButton(tid, "claim", label="我來做", emoji="🙋", row=0))
    else:
        view.add_item(TaskButton(tid, "reopen", label="重新打開", emoji="↩️", row=0))
    view.add_item(TaskButton(tid, "edit", label="編輯", emoji="✏️", row=1))
    view.add_item(TaskButton(tid, "assign", label="指派", emoji="👤", row=1))
    view.add_item(TaskButton(tid, "delete", label="刪除", emoji="🗑️", style=discord.ButtonStyle.danger, row=1))
    return view


class Tasks(commands.Cog):
    task_group = app_commands.Group(name="task", description="待辦與專案管理：建立任務、指派負責人、追蹤進度",
                                    guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(TaskButton)
        self.send_notices.start()

    async def cog_unload(self):
        self.bot.remove_dynamic_items(TaskButton)
        self.send_notices.cancel()

    # ------------------------------------------------------------------ 建立
    async def card_embed(self, task) -> discord.Embed:
        """卡片內容：多查專案與里程碑的名稱。"""
        project = milestone = None
        if task["project_id"]:
            row = await self.bot.db.get_project(task["project_id"])
            project = row["name"] if row else None
        if task["milestone_id"]:
            row = await self.bot.db.get_milestone(task["milestone_id"])
            milestone = row["name"] if row else None
        return build_embed(task, project, milestone)

    async def resolve_project(self, guild_id: int, channel_id: int, project_id: Optional[int],
                              milestone_id: Optional[int]):
        """決定待辦屬於哪個專案與里程碑，回傳 (專案 ID, 里程碑 ID, 錯誤訊息)。
        沒指定專案時：有指定里程碑就用里程碑的專案，否則用這個頻道綁定的專案。"""
        milestone = None
        if milestone_id:
            milestone = await self.bot.db.get_milestone(milestone_id)
            if not milestone:
                return None, None, "找不到這個里程碑。"
            if project_id and milestone["project_id"] != project_id:
                return None, None, "這個里程碑不屬於選擇的專案。"
            project_id = milestone["project_id"]
        if project_id:
            project = await self.bot.db.get_project(project_id)
            if not project or project["guild_id"] != guild_id:
                return None, None, "找不到這個專案。"
            if project["archived"]:
                return None, None, f"專案「{project['name']}」已經封存了。"
            return project_id, milestone_id, None
        project = await self.bot.db.project_for_channel(guild_id, channel_id)
        return (project["id"] if project else None), None, None

    async def create(self, interaction: discord.Interaction, title: str, due: Optional[str] = None,
                     priority: Optional[str] = None, note: str = "", assignee_id: Optional[int] = None,
                     project_id: Optional[int] = None, milestone_id: Optional[int] = None,
                     repeat: Optional[str] = None) -> None:
        if not interaction.guild:
            return await send_error(interaction, "待辦只能在伺服器裡使用。")
        if repeat and not (due and due.strip()):
            return await send_error(interaction, "重複的待辦要設定截止時間，才知道下一次是什麼時候。")
        project_id, milestone_id, problem = await self.resolve_project(
            interaction.guild.id, interaction.channel_id, project_id, milestone_id)
        if problem:
            return await send_error(interaction, problem)
        title = title.strip()
        if not title:
            return await send_error(interaction, "請寫上待辦的內容。")
        level = tk.parse_priority(priority)
        if level is None:
            return await send_error(interaction, "優先度請填「高」、「中」或「低」。")
        due_at = remind = None
        has_time = False
        if due and due.strip():
            parsed = tk.parse_due(due, now_tw())
            if not parsed:
                return await send_error(interaction, f"看不懂「{due}」這個截止時間，或是時間已經過了。\n{DUE_HELP}")
            due_at, has_time = parsed.at.timestamp(), parsed.has_time
            remind = tk.remind_at(due_at, time.time())
        if await self.bot.db.count_open_tasks(interaction.guild.id) >= MAX_OPEN_PER_GUILD:
            return await send_error(interaction, f"這個伺服器已經有 {MAX_OPEN_PER_GUILD} 個未完成的待辦了，先完成或刪掉一些吧。")

        task_id = await self.bot.db.add_task(
            interaction.guild.id, interaction.channel_id, interaction.user.id, title[:200], note=note.strip()[:1000],
            assignee_id=assignee_id, priority=level, due_at=due_at, due_has_time=has_time, remind_at=remind,
            project_id=project_id, milestone_id=milestone_id, repeat=repeat)
        task = await self.bot.db.get_task(task_id)
        mention = f"<@{assignee_id}> 你有一個新的待辦" if assignee_id and assignee_id != interaction.user.id else None
        message = await respond(interaction, content=mention, embed=await self.card_embed(task), view=build_view(task),
                                allowed_mentions=ALLOWED)
        await self.bot.db.set_task_message(task_id, interaction.channel_id, message.id)
        log.info("待辦 #%d 建立：%s", task_id, title[:40])

    @task_group.command(name="add", description="新增待辦，會在頻道發一張可以用按鈕更新進度的卡片")
    @app_commands.describe(title="要做的事", assignee="負責人（不填就是還沒有人負責）",
                           due="截止時間，例如 明天、週五、11/20、明天 18:00", priority="優先度（預設中）",
                           note="補充說明", project="屬於哪個專案（不填就用這個頻道的專案）",
                           milestone="屬於哪個里程碑", repeat="重複（完成後自動建立下一次，需要截止時間）")
    @app_commands.choices(priority=[app_commands.Choice(name=f"{tk.PRIORITY_EMOJI[k]} {v}", value=v)
                                    for k, v in sorted(tk.PRIORITIES.items(), reverse=True)],
                          repeat=[app_commands.Choice(name=f"🔁 {v}", value=k) for k, v in tk.REPEATS.items()])
    async def add(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 200],
                  assignee: Optional[discord.Member] = None, due: Optional[str] = None,
                  priority: Optional[app_commands.Choice[str]] = None,
                  note: Optional[app_commands.Range[str, 1, 1000]] = None,
                  project: Optional[int] = None, milestone: Optional[int] = None,
                  repeat: Optional[app_commands.Choice[str]] = None):
        if assignee and assignee.bot:
            return await send_error(interaction, "不能把待辦指派給機器人。")
        await self.create(interaction, title, due, priority.value if priority else None, note or "",
                          assignee.id if assignee else None, project, milestone, repeat.value if repeat else None)

    async def project_choices(self, interaction: discord.Interaction, current: str):
        if not interaction.guild_id:
            return []
        current = current.strip().lower()
        rows = await self.bot.db.list_projects(interaction.guild_id)
        return [app_commands.Choice(name=f"📁 {r['name']}"[:100], value=r["id"])
                for r in rows if not current or current in r["name"].lower()][:25]

    async def milestone_choices(self, interaction: discord.Interaction, current: str):
        if not interaction.guild_id:
            return []
        project_id = getattr(interaction.namespace, "project", None)
        if not project_id:
            project = await self.bot.db.project_for_channel(interaction.guild_id, interaction.channel_id)
            project_id = project["id"] if project else None
        if not project_id:
            return []
        current = current.strip().lower()
        rows = await self.bot.db.list_milestones(int(project_id))
        return [app_commands.Choice(name=f"🏁 {r['name']}（{r['done']}/{r['total']}）"[:100], value=r["id"])
                for r in rows if not current or current in r["name"].lower()][:25]

    @add.autocomplete("project")
    async def add_project_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.project_choices(interaction, current)

    @add.autocomplete("milestone")
    async def add_milestone_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.milestone_choices(interaction, current)

    @add.autocomplete("due")
    async def due_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        now = now_tw()
        choices = []
        for text in ([current] if current else []) + [p for p in DUE_PRESETS if p != current]:
            parsed = tk.parse_due(text, now)
            if parsed:
                label = f"{text}　→　{tk.fmt_due(parsed.at.timestamp(), parsed.has_time)} 截止"
                choices.append(app_commands.Choice(name=label[:100], value=text[:100]))
        if current and not tk.parse_due(current, now):
            choices.insert(0, app_commands.Choice(name=f"⚠️ 看不懂：{current}"[:100], value=current[:100]))
        return choices[:25]

    async def open_modal(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return await send_error(interaction, "待辦只能在伺服器裡使用。")
        await interaction.response.send_modal(TaskModal(self))

    # ------------------------------------------------------------------ 清單
    def list_embed(self, rows, scope: str, include_done: bool, project: Optional[str] = None) -> discord.Embed:
        title = f"📋 待辦清單・{f'📁 {project}・' if project else ''}{SCOPES[scope]}" + ("（含已完成）" if include_done else "")
        if not rows:
            return make_embed(title, "目前沒有待辦。用 `/task add` 新增一個吧！", Color.MUTED)
        now = time.time()
        lines = []
        for r in rows:
            who = f"<@{r['assignee_id']}>" if r["assignee_id"] else "沒人負責"
            due = ""
            if r["due_at"] is not None:
                due = f"・{'⚠️ ' if is_overdue(r, now) else ''}{ts(r['due_at'], 'R')}截止"
            lines.append(f"{tk.STATUS_EMOJI[r['status']]}{tk.PRIORITY_EMOJI[r['priority']]} `#{r['id']}` "
                         f"**{r['title'][:60]}**\n　{who}{due}")
        embed = make_embed(title, "\n".join(lines), Color.PRIMARY)
        footer = "從下方選單打開任務卡片"
        if len(rows) >= LIST_LIMIT:
            footer = f"只顯示前 {LIST_LIMIT} 個・" + footer
        embed.set_footer(text=f"📝 待辦 🔨 進行中 ✅ 完成・🔴 高 🟡 中 🟢 低・{footer}")
        return embed

    async def send_list(self, interaction: discord.Interaction, scope: str = "all", include_done: bool = False,
                        project_id: Optional[int] = None) -> None:
        if not interaction.guild:
            return await send_error(interaction, "待辦只能在伺服器裡使用。")
        project_name = None
        if project_id:
            project = await self.bot.db.get_project(project_id)
            if not project or project["guild_id"] != interaction.guild.id:
                return await send_error(interaction, "找不到這個專案。")
            project_name = project["name"]
        await defer(interaction, ephemeral=True)
        rows = await self.bot.db.list_tasks(interaction.guild.id, scope=scope, user_id=interaction.user.id,
                                            include_done=include_done, limit=LIST_LIMIT, project_id=project_id)
        view = TaskListView(self, interaction.user.id, rows) if rows else None
        kwargs = {"view": view} if view else {}
        sent = await respond(interaction, embed=self.list_embed(rows, scope, include_done, project_name),
                             ephemeral=True, **kwargs)
        if view:
            view.message = sent

    @task_group.command(name="list", description="查看伺服器的待辦")
    @app_commands.describe(scope="要看哪些（預設全部）", include_done="也列出已完成的", project="只看某個專案")
    @app_commands.choices(scope=[app_commands.Choice(name=v, value=k) for k, v in SCOPES.items()])
    async def list_command(self, interaction: discord.Interaction,
                           scope: Optional[app_commands.Choice[str]] = None, include_done: bool = False,
                           project: Optional[int] = None):
        await self.send_list(interaction, scope.value if scope else "all", include_done, project)

    @list_command.autocomplete("project")
    async def list_project_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.project_choices(interaction, current)

    # ------------------------------------------------------------------ 直接完成
    @task_group.command(name="done", description="把一個待辦標記為完成")
    @app_commands.describe(task="要完成的待辦")
    async def done_command(self, interaction: discord.Interaction, task: int):
        row = await self.bot.db.get_task(task)
        if not row or row["guild_id"] != interaction.guild_id:
            return await send_error(interaction, f"找不到待辦 #{task}。")
        if not can_edit(row, interaction.user):
            return await send_error(interaction, "只有建立者、負責人或管理員可以更新這個待辦。")
        if not await self.complete(task, interaction.user.id, refresh=True):
            return await send_error(interaction, f"#{task} 已經完成了。")
        await respond(interaction, embed=success_embed(f"#{task}「{row['title']}」完成了，辛苦了！"))

    @done_command.autocomplete("task")
    async def open_task_autocomplete(self, interaction: discord.Interaction, current: str):
        if not interaction.guild_id:
            return []
        rows = await self.bot.db.list_tasks(interaction.guild_id, scope="all", limit=100)
        current = current.strip().lstrip("#")
        choices = []
        for r in rows:
            if current and current not in str(r["id"]) and current.lower() not in r["title"].lower():
                continue
            name = f"#{r['id']} {tk.STATUS_EMOJI[r['status']]} {r['title']}"
            choices.append(app_commands.Choice(name=name[:100], value=r["id"]))
        return choices[:25]

    # ------------------------------------------------------------------ 卡片按鈕
    async def handle_button(self, interaction: discord.Interaction, task_id: int, action: str) -> None:
        task = await self.bot.db.get_task(task_id)
        if not task:
            return await send_error(interaction, "這個待辦已經被刪除了。")
        user = interaction.user

        if action == "claim":
            if task["assignee_id"]:
                return await send_error(interaction, f"已經有人負責了：<@{task['assignee_id']}>。")
            await self.bot.db.update_task(task_id, assignee_id=user.id)
            return await self.show_updated(interaction, task_id)

        if action in ("doing", "done", "reopen", "edit", "assign") and not can_edit(task, user):
            return await send_error(interaction, "只有建立者、負責人或管理員可以更新這個待辦。")
        if action == "delete" and not can_delete(task, user):
            return await send_error(interaction, "只有建立者或管理員可以刪除這個待辦。")

        if action == "done":
            await self.show_completed(interaction, task_id, user.id)
            return
        if action in ("doing", "reopen"):
            status = "todo" if action == "reopen" else action
            await self.bot.db.set_task_status(task_id, status, user.id)
            return await self.show_updated(interaction, task_id)
        if action == "edit":
            return await interaction.response.send_modal(TaskModal(self, task))
        if action == "assign":
            view = AssignView(self, user.id, task_id)
            await interaction.response.send_message(f"要把 #{task_id}「{task['title'][:60]}」指派給誰？",
                                                    view=view, ephemeral=True)
            view.message = await interaction.original_response()
            return
        if action == "delete":
            view = ConfirmDeleteView(self, user.id, task_id)
            await interaction.response.send_message(f"確定要刪除 #{task_id}「{task['title'][:60]}」嗎？刪除後無法復原。",
                                                    view=view, ephemeral=True)
            view.message = await interaction.original_response()
            return
        await send_error(interaction, "這個按鈕已經失效了。")

    async def show_completed(self, interaction: discord.Interaction, task_id: int, user_id: int) -> None:
        """卡片按鈕按「完成」：先更新卡片（Discord 要求 3 秒內回應），再處理獎勵和週期任務。"""
        if not await self.bot.db.set_task_status(task_id, "done", user_id):
            return await self.show_updated(interaction, task_id)
        await self.show_updated(interaction, task_id)
        await self.after_complete(task_id)

    async def complete(self, task_id: int, user_id: int, *, refresh: bool = True) -> bool:
        """把待辦標記為完成，並處理獎勵與週期任務。已經是完成狀態就回傳 False。
        user_id 為 0 代表從管理後台或 GitHub 完成。"""
        if not await self.bot.db.set_task_status(task_id, "done", user_id):
            return False
        if refresh:
            task = await self.bot.db.get_task(task_id)
            if task:
                await self.refresh_card(task)
        await self.after_complete(task_id)
        return True

    async def after_complete(self, task_id: int) -> None:
        task = await self.bot.db.get_task(task_id)
        if not task:
            return
        notes = []
        amount = tk.task_reward(task["priority"], task["creator_id"], task["assignee_id"])
        if amount:
            since = tk.day_start(now_tw()).timestamp()
            paid, state = await self.bot.db.reward_task(task_id, task["assignee_id"], amount, since,
                                                        tk.REWARD_DAILY_CAP, tk.REWARD_REASON)
            if paid:
                notes.append(f"💰 <@{task['assignee_id']}> 完成待辦，獲得 **{fmt_points(paid)}**")
            elif state == "capped":
                notes.append(f"<@{task['assignee_id']}> 今天的待辦獎勵（每天 {tk.REWARD_DAILY_CAP} 次）已經領完了")
        if task["repeat"] and task["due_at"] is not None:
            new_id, due = await self.spawn_next(task)
            notes.append(f"🔁 已建立下一次 #{new_id}，{tk.fmt_due(due, bool(task['due_has_time']))} 截止")
        if notes:
            await self.post(task, "\n".join(notes))

    async def spawn_next(self, task):
        """週期任務完成後，建立下一次（複製內容，截止時間往後推），並在同一個頻道發新卡片。"""
        due = tk.next_due(datetime.fromtimestamp(task["due_at"], TAIPEI), task["repeat"], now_tw()).timestamp()
        new_id = await self.bot.db.add_task(
            task["guild_id"], task["channel_id"], task["creator_id"], task["title"], note=task["note"],
            assignee_id=task["assignee_id"], priority=task["priority"], due_at=due,
            due_has_time=bool(task["due_has_time"]), remind_at=tk.remind_at(due, time.time()),
            project_id=task["project_id"], milestone_id=task["milestone_id"], repeat=task["repeat"])
        # 舊的那筆不再是週期任務，重新打開再完成也不會又多建一個
        await self.bot.db.update_task(task["id"], repeat=None)
        new = await self.bot.db.get_task(new_id)
        try:
            channel = self.bot.get_channel(task["channel_id"]) or await self.bot.fetch_channel(task["channel_id"])
            message = await channel.send(embed=await self.card_embed(new), view=build_view(new),
                                         allowed_mentions=discord.AllowedMentions.none())
            await self.bot.db.set_task_message(new_id, task["channel_id"], message.id)
        except discord.HTTPException:
            log.warning("週期待辦 #%d 的新卡片發送失敗（任務已建立）", new_id)
        log.info("週期待辦 #%d 完成，建立下一次 #%d", task["id"], new_id)
        return new_id, due

    async def show_updated(self, interaction: discord.Interaction, task_id: int) -> None:
        """更新按下按鈕的那則訊息；如果按的不是頻道裡的卡片（例如清單打開的副本），也一併更新卡片。"""
        task = await self.bot.db.get_task(task_id)
        if not task:
            return await send_error(interaction, "這個待辦已經被刪除了。")
        await interaction.response.edit_message(embed=await self.card_embed(task), view=build_view(task))
        if not interaction.message or interaction.message.id != task["message_id"]:
            await self.refresh_card(task)

    async def refresh_card(self, task) -> None:
        if not task["message_id"]:
            return
        try:
            channel = self.bot.get_channel(task["channel_id"]) or await self.bot.fetch_channel(task["channel_id"])
            await channel.get_partial_message(task["message_id"]).edit(embed=await self.card_embed(task),
                                                                       view=build_view(task))
        except (discord.NotFound, discord.Forbidden):
            pass   # 卡片被刪掉或沒有權限，資料庫裡的任務照樣存在
        except discord.HTTPException:
            log.warning("更新待辦 #%s 的卡片失敗", task["id"], exc_info=True)

    async def assign(self, interaction: discord.Interaction, task_id: int, member: discord.abc.User) -> None:
        if member.bot:
            return await send_error(interaction, "不能把待辦指派給機器人。")
        await self.bot.db.update_task(task_id, assignee_id=member.id)
        task = await self.bot.db.get_task(task_id)
        if not task:
            return await send_error(interaction, "這個待辦已經被刪除了。")
        await interaction.response.edit_message(content=f"已指派給 {member.mention}。", view=None)
        await self.refresh_card(task)
        if member.id != interaction.user.id:
            await self.post(task, f"📌 <@{member.id}> 你被指派了待辦 #{task_id}「{task['title']}」")

    async def delete(self, interaction: discord.Interaction, task_id: int) -> None:
        task = await self.bot.db.get_task(task_id)
        if not task:
            return await interaction.response.edit_message(content="這個待辦已經刪除了。", view=None)
        await self.bot.db.delete_task(task_id)
        await interaction.response.edit_message(content=f"已刪除 #{task_id}「{task['title'][:60]}」。", view=None)
        if task["message_id"]:
            try:
                channel = self.bot.get_channel(task["channel_id"]) or await self.bot.fetch_channel(task["channel_id"])
                deleted = make_embed(f"🗑️ #{task_id} {task['title']}"[:256], f"已被 <@{interaction.user.id}> 刪除",
                                     Color.MUTED)
                await channel.get_partial_message(task["message_id"]).edit(content=None, embed=deleted, view=None)
            except discord.HTTPException:
                pass
        log.info("待辦 #%d 已刪除", task_id)

    async def save_edit(self, interaction: discord.Interaction, task, title: str, due: str, priority: str,
                        note: str) -> None:
        level = tk.parse_priority(priority)
        if level is None:
            return await send_error(interaction, "優先度請填「高」、「中」或「低」。")
        fields = {"title": title.strip()[:200] or task["title"], "note": note.strip()[:1000], "priority": level}
        due = due.strip()
        if due != (task_due_input(task) or ""):
            if due:
                parsed = tk.parse_due(due, now_tw())
                if not parsed:
                    return await send_error(interaction, f"看不懂「{due}」這個截止時間，或是時間已經過了。\n{DUE_HELP}")
                fields.update(due_at=parsed.at.timestamp(), due_has_time=int(parsed.has_time),
                              remind_at=tk.remind_at(parsed.at.timestamp(), time.time()))
            else:
                fields.update(due_at=None, due_has_time=0, remind_at=None)
        await self.bot.db.update_task(task["id"], **fields)
        await self.show_updated(interaction, task["id"])

    # ------------------------------------------------------------------ 到期提醒
    @tasks.loop(seconds=60)
    async def send_notices(self):
        for kind, task in await self.bot.db.due_task_notices(time.time()):
            try:
                if kind == "remind":
                    text = f"⏰ <@{owner_of(task)}> 待辦 #{task['id']}「{task['title']}」{ts(task['due_at'], 'R')}截止"
                else:
                    text = f"⚠️ <@{owner_of(task)}> 待辦 #{task['id']}「{task['title']}」已經逾期了"
                    await self.refresh_card(task)   # 卡片變紅
                await self.post(task, text)
            except Exception:
                log.exception("送出待辦 #%s 的通知失敗", task["id"])
            await self.bot.db.mark_task_notice(task["id"], kind)

    @send_notices.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def post(self, task, text: str) -> None:
        """在卡片的頻道發一則訊息，回覆在卡片上方便點回去。"""
        try:
            channel = self.bot.get_channel(task["channel_id"]) or await self.bot.fetch_channel(task["channel_id"])
            reference = None
            if task["message_id"]:
                reference = discord.MessageReference(message_id=task["message_id"], channel_id=task["channel_id"],
                                                     fail_if_not_exists=False)
            await channel.send(text, allowed_mentions=ALLOWED, reference=reference, mention_author=False)
        except (discord.NotFound, discord.Forbidden):
            log.info("待辦 #%s 的頻道已經不存在或沒有權限，略過通知", task["id"])


def task_due_input(task) -> Optional[str]:
    """編輯表單裡預先填好的截止時間（例如「11/20」「11/20 18:00」）。"""
    if task["due_at"] is None:
        return None
    at = datetime.fromtimestamp(task["due_at"], TAIPEI)
    return f"{at.month}/{at.day}" + (f" {at:%H:%M}" if task["due_has_time"] else "")


class TaskModal(discord.ui.Modal):
    """新增（task 為 None）或編輯待辦的表單。"""

    def __init__(self, cog: Tasks, task=None):
        super().__init__(title="編輯待辦" if task else "新增待辦")
        self.cog, self.task = cog, task
        self.title_input = discord.ui.TextInput(label="要做的事", max_length=200,
                                                default=task["title"] if task else None)
        self.due = discord.ui.TextInput(label="截止時間（可不填）", required=False, max_length=40,
                                        placeholder="明天、週五、11/20、明天 18:00",
                                        default=task_due_input(task) if task else None)
        self.priority = discord.ui.TextInput(label="優先度：高、中、低", required=False, max_length=4,
                                             default=tk.PRIORITIES[task["priority"]] if task else "中")
        self.note = discord.ui.TextInput(label="補充說明（可不填）", style=discord.TextStyle.paragraph, required=False,
                                         max_length=1000, default=(task["note"] or None) if task else None)
        for item in (self.title_input, self.due, self.priority, self.note):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if self.task:
            current = await self.cog.bot.db.get_task(self.task["id"])
            if not current:
                return await send_error(interaction, "這個待辦已經被刪除了。")
            return await self.cog.save_edit(interaction, current, self.title_input.value, self.due.value,
                                            self.priority.value, self.note.value)
        await self.cog.create(interaction, self.title_input.value, self.due.value, self.priority.value, self.note.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("待辦表單發生錯誤", exc_info=error)
        await send_error(interaction, "處理表單時發生錯誤，請稍後再試。")


class AssignView(BaseView):
    def __init__(self, cog: Tasks, owner_id: int, task_id: int):
        super().__init__(owner_id=owner_id, timeout=120)
        self.cog, self.task_id = cog, task_id
        select = discord.ui.UserSelect(placeholder="選擇負責人")
        select.callback = self.on_select
        self.add_item(select)
        self.select = select

    async def on_select(self, interaction: discord.Interaction):
        await self.cog.assign(interaction, self.task_id, self.select.values[0])


class ConfirmDeleteView(BaseView):
    def __init__(self, cog: Tasks, owner_id: int, task_id: int):
        super().__init__(owner_id=owner_id, timeout=60)
        self.cog, self.task_id = cog, task_id

    @discord.ui.button(label="確定刪除", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.delete(interaction, self.task_id)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="沒有刪除。", view=None)


class TaskListView(BaseView):
    def __init__(self, cog: Tasks, owner_id: int, rows):
        super().__init__(owner_id=owner_id, timeout=300)
        self.cog = cog
        select = discord.ui.Select(
            placeholder="打開任務卡片",
            options=[discord.SelectOption(label=f"#{r['id']} {r['title']}"[:100], value=str(r["id"]),
                                          emoji=tk.STATUS_EMOJI[r["status"]],
                                          description=(tk.fmt_due(r["due_at"], bool(r["due_has_time"])) + " 截止"
                                                       if r["due_at"] is not None else None))
                     for r in rows[:25]],
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        task = await self.cog.bot.db.get_task(int(interaction.data["values"][0]))
        if not task:
            return await send_error(interaction, "這個待辦已經被刪除了。")
        # 私密的卡片副本，按鈕跟頻道裡的卡片一樣，操作後兩邊都會更新
        await interaction.response.send_message(embed=await self.cog.card_embed(task), view=build_view(task),
                                                ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tasks(bot))
