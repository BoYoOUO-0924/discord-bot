"""音樂：在語音頻道播放 YouTube 的歌，有播放清單和控制按鈕。

- 點歌：/play 歌名或 YouTube 網址、大廳的「音樂」按鈕，或 @嘎哇 放周杰倫的晴天
- 點歌的人要先在語音頻道裡，嘎哇會進那個頻道播放
- 控制面板：⏮️ 上一首、⏯️ 暫停/繼續、⏭️ 下一首、📜 播放清單、⏹️ 停止
  只有同一個語音頻道裡的人能按
- 沒在放歌 10 分鐘，或語音頻道只剩嘎哇 1 分鐘，就自動離開

伺服器需要安裝 ffmpeg（Ubuntu：sudo apt install ffmpeg）；yt-dlp、deno 由 requirements.txt 安裝。
"""
import asyncio
import logging
import shutil
import time
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import music
from core.music import MusicError, Playlist, Track, fmt_duration
from core.ui import Color, defer, make_embed, respond, send_error

log = logging.getLogger(__name__)

IDLE_LEAVE = 600     # 沒在播放（含暫停）幾秒後離開
ALONE_LEAVE = 60     # 語音頻道沒人幾秒後離開


class Player:
    """一個伺服器的播放狀態。"""

    def __init__(self, guild: discord.Guild, text_channel: discord.abc.Messageable):
        self.guild = guild
        self.text_channel = text_channel
        self.queue = Playlist()
        self.panel: Optional[discord.Message] = None
        self.view: Optional["PanelView"] = None
        self.generation = 0          # 每換一首加 1；舊歌的「播完了」通知對不上就忽略
        self.lock = asyncio.Lock()
        self.idle_since = time.time()
        self.alone_since: Optional[float] = None
        self.loading = False

    @property
    def voice(self) -> Optional[discord.VoiceClient]:
        return self.guild.voice_client

    @property
    def playing(self) -> bool:
        return bool(self.voice and self.voice.is_playing())

    @property
    def paused(self) -> bool:
        return bool(self.voice and self.voice.is_paused())

    @property
    def busy(self) -> bool:
        return self.playing or self.paused or self.loading


class PanelView(discord.ui.View):
    """控制面板。同一個語音頻道裡的人都能按。"""

    def __init__(self, cog: "Music", player: Player):
        super().__init__(timeout=None)
        self.cog, self.player = cog, player
        self.refresh()

    def refresh(self) -> None:
        self.prev.disabled = not self.player.queue.has_prev
        self.next.disabled = not self.player.queue.has_next
        self.pause.emoji = "▶️" if self.player.paused else "⏸️"
        self.pause.label = "繼續" if self.player.paused else "暫停"
        self.pause.disabled = not (self.player.playing or self.player.paused)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        voice = self.player.voice
        member_voice = getattr(interaction.user, "voice", None)
        if not voice or not member_voice or member_voice.channel != voice.channel:
            await send_error(interaction, "要在嘎哇所在的語音頻道裡才能控制喔！")
            return False
        return True

    async def _do(self, interaction: discord.Interaction, action: str) -> None:
        try:
            note = await self.cog.control(self.player.guild, action)
        except MusicError as e:
            return await send_error(interaction, str(e))
        if action == "queue":
            return await interaction.response.send_message(embed=self.cog.queue_embed(self.player), ephemeral=True)
        if not interaction.response.is_done():
            await interaction.response.defer()
        log.info("音樂：%s 按了 %s（%s）", interaction.user, action, note)

    @discord.ui.button(emoji="⏮️", label="上一首", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do(interaction, "prev")

    @discord.ui.button(emoji="⏸️", label="暫停", style=discord.ButtonStyle.primary)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do(interaction, "resume" if self.player.paused else "pause")

    @discord.ui.button(emoji="⏭️", label="下一首", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do(interaction, "next")

    @discord.ui.button(emoji="📜", label="播放清單", style=discord.ButtonStyle.secondary)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do(interaction, "queue")

    @discord.ui.button(emoji="⏹️", label="停止", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do(interaction, "stop")

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        log.exception("音樂面板發生錯誤", exc_info=error)
        await send_error(interaction, "操作時發生錯誤，請稍後再試。")


class SongModal(discord.ui.Modal, title="點歌"):
    query = discord.ui.TextInput(label="歌名、歌手或 YouTube 網址", placeholder="周杰倫 晴天", max_length=200)

    def __init__(self, cog: "Music"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.play_from_interaction(interaction, self.query.value)


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players: Dict[int, Player] = {}
        self.ffmpeg = shutil.which("ffmpeg")

    async def cog_load(self):
        if self.ffmpeg:
            log.info("音樂功能已啟用")
        else:
            log.warning("找不到 ffmpeg，音樂功能無法播放（Ubuntu：sudo apt install ffmpeg）")
        self.watchdog.start()

    async def cog_unload(self):
        self.watchdog.cancel()
        for guild_id in list(self.players):
            await self.leave(guild_id, None)

    # ------------------------------------------------------------------ 點歌
    def voice_target(self, member) -> discord.VoiceChannel:
        """檢查點歌的人能不能讓嘎哇去放歌，回傳要進的語音頻道。"""
        if not self.ffmpeg:
            raise MusicError("伺服器還沒裝好放音樂需要的程式（ffmpeg），請管理員處理。")
        guild = getattr(member, "guild", None)
        channel = getattr(getattr(member, "voice", None), "channel", None)
        if not guild:
            raise MusicError("音樂只能在伺服器裡使用。")
        if not channel:
            raise MusicError("你要先進入語音頻道，我才知道要去哪裡放歌喔！")
        perms = channel.permissions_for(guild.me)
        if not (perms.connect and perms.speak):
            raise MusicError(f"我沒有權限進入「{channel.name}」或在裡面說話，請管理員幫忙開權限。")
        player = self.players.get(guild.id)
        voice = guild.voice_client
        if voice and voice.channel != channel and player and player.busy:
            raise MusicError(f"我正在「{voice.channel.name}」放歌，要點歌請進那個語音頻道。")
        return channel

    async def request(self, member: discord.Member, text_channel, query: str) -> str:
        """點歌：找歌、進語音頻道、加入清單，沒在播就開始播。回傳要告訴玩家的話。"""
        channel = self.voice_target(member)
        guild = member.guild
        tracks = await music.search(query, member.id, member.display_name)

        player = self.players.get(guild.id)
        if player is None:
            player = self.players[guild.id] = Player(guild, text_channel)
        async with player.lock:
            voice = guild.voice_client
            if voice is None:
                try:
                    await channel.connect(self_deaf=True, timeout=20)
                except (asyncio.TimeoutError, discord.ClientException):
                    log.warning("音樂：進入語音頻道失敗", exc_info=True)
                    raise MusicError("進不去語音頻道，等一下再試試看。") from None
            elif voice.channel != channel:
                await voice.move_to(channel)
            player.text_channel = player.text_channel or text_channel
            before = len(player.queue.tracks)
            added = player.queue.add(tracks)
            if not added:
                raise MusicError(f"播放清單已經滿了（最多 {music.MAX_QUEUE} 首），先聽完一些吧。")
            if not player.busy:
                await self._play_at(player, before)
                note = f"🎵 開始播放 **{tracks[0].title}**"
            else:
                note = f"➕ 已加入播放清單第 {before + 1} 首：**{tracks[0].title}**"
            if added > 1:
                note += f"\n（從播放清單一共加入 {added} 首）"
            elif added < len(tracks):
                note += "\n（播放清單滿了，有些沒加進去）"
        await self.update_panel(player)
        log.info("音樂：%s 點了 %s（%d 首）", member, tracks[0].title, added)
        return note

    async def play_from_interaction(self, interaction: discord.Interaction, query: str) -> None:
        try:
            self.voice_target(interaction.user)
            music.check_query(query)
        except MusicError as e:
            return await send_error(interaction, str(e))
        await defer(interaction)
        try:
            note = await self.request(interaction.user, interaction.channel, query)
        except MusicError as e:
            note = f"❌ {e}"
        await respond(interaction, content=note, allowed_mentions=discord.AllowedMentions.none())

    # ------------------------------------------------------------------ 播放
    async def _play_at(self, player: Player, index: int) -> None:
        """從清單第 index 首開始播；播不了的歌自動跳過。呼叫前要拿到 player.lock。"""
        voice = player.voice
        if not voice:
            return
        player.generation += 1
        generation = player.generation
        if voice.is_playing() or voice.is_paused():
            voice.stop()     # 舊歌的「播完了」通知會因為 generation 對不上被忽略
        player.loading = True
        try:
            while 0 <= index < len(player.queue.tracks):
                player.queue.index = index
                track = player.queue.tracks[index]
                try:
                    stream = await music.stream_url(track)
                    errors = music.ErrorTail()
                    source = discord.FFmpegOpusAudio(stream.url, executable=self.ffmpeg, stderr=errors,
                                                     before_options=music.ffmpeg_before(stream.headers),
                                                     options=music.FFMPEG_OPTIONS)
                except MusicError as e:
                    await self._notice(player, f"⚠️ 跳過「{track.title}」：{e}")
                    index += 1
                    continue
                if generation != player.generation or not voice.is_connected():
                    source.cleanup()
                    return
                started = time.monotonic()
                voice.play(source, after=lambda error, g=generation: self._after(player, g, error, started, errors))
                return
            player.queue.index = len(player.queue.tracks) - 1   # 全部都播不了
        finally:
            player.loading = False
            player.idle_since = time.time()

    def _after(self, player: Player, generation: int, error: Optional[Exception],
               started: float, errors: "music.ErrorTail") -> None:
        """播放執行緒呼叫：一首歌結束（播完、被跳過或出錯）。"""
        failed = error is not None or time.monotonic() - started < music.MIN_PLAY_SECONDS
        if failed and generation == player.generation:
            log.warning("音樂：播放失敗（%.1f 秒就結束）：%s\nffmpeg：%s", time.monotonic() - started,
                        error or "沒有錯誤訊息", errors.text() or "（沒有輸出）")
        asyncio.run_coroutine_threadsafe(self._track_ended(player, generation, failed), self.bot.loop)

    async def _track_ended(self, player: Player, generation: int, failed: bool = False) -> None:
        async with player.lock:
            if generation != player.generation or self.players.get(player.guild.id) is not player:
                return
            player.idle_since = time.time()
            if failed and player.queue.current:
                await self._notice(player, f"⚠️ 「{player.queue.current.title}」播放失敗，跳過。"
                                           "（原因寫在 bot 紀錄裡，可以用 tools/music_check.py 檢查）")
            if player.queue.has_next:
                await self._play_at(player, player.queue.index + 1)
        await self.update_panel(player)

    async def control(self, guild: discord.Guild, action: str) -> str:
        """控制播放：next/prev/pause/resume/stop/queue。回傳簡短說明。"""
        player = self.players.get(guild.id)
        if not player or not player.voice:
            raise MusicError("現在沒有在放歌喔。")
        if action == "queue":
            return "播放清單"
        if action == "stop":
            await self.leave(guild.id, "⏹️ 已停止播放，嘎哇離開語音頻道。")
            return "已停止播放"
        async with player.lock:
            if action == "pause":
                if not player.playing:
                    raise MusicError("現在沒有在播放。")
                player.voice.pause()
                note = "已暫停"
            elif action == "resume":
                if not player.paused:
                    raise MusicError("現在沒有暫停。")
                player.voice.resume()
                note = "繼續播放"
            elif action == "next":
                if not player.queue.has_next:
                    raise MusicError("已經是最後一首了，再點幾首歌吧！")
                await self._play_at(player, player.queue.index + 1)
                note = f"下一首：{player.queue.current.title}"
            elif action == "prev":
                if not player.queue.has_prev:
                    raise MusicError("已經是第一首了。")
                await self._play_at(player, player.queue.index - 1)
                note = f"上一首：{player.queue.current.title}"
            else:
                raise MusicError("看不懂這個指令。")
        await self.update_panel(player)
        return note

    async def leave(self, guild_id: int, reason: Optional[str]) -> None:
        player = self.players.pop(guild_id, None)
        if not player:
            return
        player.generation += 1
        voice = player.voice
        if voice:
            if voice.is_playing() or voice.is_paused():
                voice.stop()
            try:
                await voice.disconnect(force=True)
            except discord.HTTPException:
                pass
        if player.view:
            for item in player.view.children:
                item.disabled = True
            player.view.stop()
        if player.panel:
            embed = make_embed("⏹️ 播放結束", reason or "嘎哇離開語音頻道了。", Color.MUTED)
            try:
                await player.panel.edit(embed=embed, view=player.view)
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------ 畫面
    def panel_embed(self, player: Player) -> discord.Embed:
        track: Optional[Track] = player.queue.current
        if player.paused:
            title, color = "⏸️ 已暫停", Color.WARNING
        elif player.playing or player.loading:
            title, color = "🎵 正在播放", Color.SUCCESS
        else:
            title, color = "💤 播放清單放完了", Color.MUTED
        description = f"**[{track.title}]({track.url})**" if track else "清單是空的"
        if not (player.playing or player.paused or player.loading):
            description += "\n用 /play 或 @嘎哇 再點幾首歌吧！"
        embed = make_embed(title, description, color)
        if track:
            embed.add_field(name="長度", value=fmt_duration(track.duration))
            embed.add_field(name="點歌", value=track.requester_name or "?")
            embed.add_field(name="清單", value=f"第 {player.queue.index + 1} / {len(player.queue.tracks)} 首")
            if track.thumbnail:
                embed.set_thumbnail(url=track.thumbnail)
        upcoming = player.queue.upcoming
        if upcoming:
            lines = [f"`{i}.` {t.title[:50]}" for i, t in enumerate(upcoming[:3], player.queue.index + 2)]
            if len(upcoming) > 3:
                lines.append(f"……還有 {len(upcoming) - 3} 首")
            embed.add_field(name="接下來", value="\n".join(lines), inline=False)
        voice = player.voice
        embed.set_footer(text=f"語音頻道：{voice.channel.name}" if voice else "")
        return embed

    def queue_embed(self, player: Player) -> discord.Embed:
        lines = player.queue.lines()
        return make_embed(f"📜 播放清單（{len(player.queue.tracks)} 首）", "\n".join(lines) or "清單是空的",
                          Color.PRIMARY)

    async def update_panel(self, player: Player, resend: bool = False) -> None:
        if self.players.get(player.guild.id) is not player:
            return
        if player.view is None:
            player.view = PanelView(self, player)
        player.view.refresh()
        embed = self.panel_embed(player)
        try:
            if player.panel and not resend:
                await player.panel.edit(embed=embed, view=player.view)
                return
        except discord.NotFound:
            pass
        except discord.HTTPException:
            log.warning("音樂：更新面板失敗", exc_info=True)
            return
        old = player.panel
        try:
            player.panel = await player.text_channel.send(embed=embed, view=player.view)
        except discord.HTTPException:
            log.warning("音樂：送出面板失敗", exc_info=True)
            return
        if old and resend:
            try:
                await old.delete()
            except discord.HTTPException:
                pass

    async def _notice(self, player: Player, text: str) -> None:
        try:
            await player.text_channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------ 給嘎哇聊天用
    async def speech(self, message: discord.Message, song: str, text: str) -> str:
        """@嘎哇 用講的點歌或控制，回傳要說的話；不行的話丟出 MusicError。"""
        action = music.parse_control(text)
        if action:
            note = await self.control(message.guild, action)
            if action == "queue":
                player = self.players[message.guild.id]
                await self.update_panel(player, resend=True)
                return "面板在這，自己看。"
            return note
        if not song:
            raise MusicError("要放什麼歌？說歌名給我聽，例如「@嘎哇 放周杰倫的晴天」。")
        return await self.request(message.author, message.channel, song)

    # ------------------------------------------------------------------ 自動離開
    @tasks.loop(seconds=30)
    async def watchdog(self):
        now = time.time()
        for guild_id, player in list(self.players.items()):
            voice = player.voice
            if not voice or not voice.is_connected():
                await self.leave(guild_id, None)
                continue
            listeners = [m for m in voice.channel.members if not m.bot]
            if listeners:
                player.alone_since = None
            elif player.alone_since is None:
                player.alone_since = now
            elif now - player.alone_since >= ALONE_LEAVE:
                await self.leave(guild_id, "大家都離開語音頻道了，本蛙也先走囉，嘎。")
                continue
            if player.playing or player.loading:
                player.idle_since = now
            elif now - player.idle_since >= IDLE_LEAVE:
                await self.leave(guild_id, "太久沒放歌，本蛙回池塘睡覺了，嘎。")

    @watchdog.before_loop
    async def before_watchdog(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # 嘎哇被踢出語音頻道
        if self.bot.user and member.id == self.bot.user.id and before.channel and after.channel is None:
            await self.leave(member.guild.id, "嘎哇被移出語音頻道了。")

    # ------------------------------------------------------------------ 指令
    async def open_modal(self, interaction: discord.Interaction) -> None:
        try:
            self.voice_target(interaction.user)
        except MusicError as e:
            return await send_error(interaction, str(e))
        await interaction.response.send_modal(SongModal(self))

    @app_commands.command(name="play", description="在你所在的語音頻道播放 YouTube 的歌（歌名或網址）")
    @app_commands.describe(query="歌名、歌手，或 YouTube 影片／播放清單網址")
    @app_commands.guild_only()
    async def play_command(self, interaction: discord.Interaction, query: str):
        await self.play_from_interaction(interaction, query)

    @app_commands.command(name="music", description="叫出音樂控制面板和播放清單")
    @app_commands.guild_only()
    async def music_command(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if not player or not player.voice:
            return await send_error(interaction, "現在沒有在放歌，用 /play 點一首吧！")
        await interaction.response.send_message(embed=self.queue_embed(player), ephemeral=True)
        player.text_channel = interaction.channel
        await self.update_panel(player, resend=True)


async def setup(bot):
    await bot.add_cog(Music(bot))
