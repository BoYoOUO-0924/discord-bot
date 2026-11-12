import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cogs import music as music_cog
from cogs.music import Music
from core import assistant as asst
from core import music
from core.music import MusicError, Playlist, Track


def track(n: int) -> Track:
    return Track(title=f"歌{n}", url=f"https://www.youtube.com/watch?v=id{n}", duration=200, requester_name="小明")


class CoreTest(unittest.TestCase):
    def test_check_query(self):
        self.assertEqual(music.check_query("  周杰倫 晴天 "), "周杰倫 晴天")
        self.assertEqual(music.check_query("聽這個 https://youtu.be/abc123"), "https://youtu.be/abc123")
        self.assertEqual(music.check_query("<https://www.youtube.com/watch?v=x>"), "https://www.youtube.com/watch?v=x")
        with self.assertRaises(MusicError):
            music.check_query("")
        with self.assertRaises(MusicError):           # 其他網站的網址不接受
            music.check_query("http://192.168.1.1/admin")

    def test_parse_control(self):
        self.assertEqual(music.parse_control("下一首"), "next")
        self.assertEqual(music.parse_control("幫我暫停一下！"), "pause")
        self.assertEqual(music.parse_control("停止播放啦"), "stop")
        self.assertIsNone(music.parse_control("下一首歌要放什麼好呢，我想聽周杰倫"))

    def test_explain_error(self):
        self.assertIn("連不上", music.explain_error('query page 1: Unable to download API page: Tunnel failed'))
        self.assertIn("年齡", music.explain_error("Sign in to confirm your age"))
        self.assertIn("擋住", music.explain_error("Sign in to confirm you're not a bot"))
        self.assertIn("不存在", music.explain_error("ERROR: Video unavailable"))

    def test_fmt_duration(self):
        self.assertEqual(music.fmt_duration(225), "3:45")
        self.assertEqual(music.fmt_duration(3723), "1:02:03")
        self.assertEqual(music.fmt_duration(None), "直播")

    def test_playlist(self):
        queue = Playlist()
        self.assertEqual(queue.add([track(i) for i in range(3)]), 3)
        queue.index = 1
        self.assertTrue(queue.has_prev and queue.has_next)
        self.assertEqual([t.title for t in queue.upcoming], ["歌2"])
        self.assertTrue(queue.lines()[1].startswith("▶️"))
        self.assertEqual(queue.add([track(i) for i in range(200)]), music.MAX_QUEUE - 3)

    def test_tracks_from_info(self):
        search = {"entries": [{"id": "abc", "title": "晴天", "url": "https://www.youtube.com/watch?v=abc",
                               "duration": 269}]}
        [t] = music.tracks_from_info(search, 5, "小明")
        self.assertEqual((t.title, t.duration, t.requester_id), ("晴天", 269, 5))
        self.assertIn("abc", t.thumbnail)
        playlist = {"entries": [{"id": "a", "title": "A", "url": "a"}, None,
                                {"id": "b", "title": "[Private video]", "url": "b"}]}
        self.assertEqual([t.url for t in music.tracks_from_info(playlist)], ["https://www.youtube.com/watch?v=a"])
        single = {"id": "z", "title": "Z", "webpage_url": "https://www.youtube.com/watch?v=z"}
        self.assertEqual(len(music.tracks_from_info(single)), 1)


class AssistantMusicTest(unittest.TestCase):
    def test_youtube_link_goes_straight_to_music(self):
        intent = asst.apply_guards("放這首 https://youtu.be/abc", asst.Intent("chat"))
        self.assertEqual((intent.name, intent.song), ("music", "https://youtu.be/abc"))

    def test_song_field_parsed(self):
        intent = asst.parse_intent({"intent": "music", "song": "周杰倫 晴天"})
        self.assertEqual((intent.name, intent.song), ("music", "周杰倫 晴天"))


# ---------------------------------------------------------------------- 假的 Discord 語音
class FakeVoice:
    def __init__(self, guild, channel):
        self.guild, self.channel = guild, channel
        self.playing = self.paused = False
        self.sources, self.after = [], None

    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused

    def is_connected(self):
        return self.guild.voice_client is self

    def play(self, source, after):
        self.sources.append(source)
        self.after = after
        self.playing, self.paused = True, False

    def stop(self):
        self.playing = self.paused = False

    def pause(self):
        self.playing, self.paused = False, True

    def resume(self):
        self.playing, self.paused = True, False

    async def move_to(self, channel):
        self.channel = channel

    async def disconnect(self, force=False):
        self.guild.voice_client = None


class FakeSource:
    def __init__(self, url, **kwargs):
        self.url = url

    def cleanup(self):
        pass


def make_world():
    guild = SimpleNamespace(id=1, me=SimpleNamespace(id=999), voice_client=None)
    voice_channel = SimpleNamespace(name="語音", members=[], permissions_for=lambda me: SimpleNamespace(connect=True, speak=True))

    async def connect(**kwargs):
        guild.voice_client = FakeVoice(guild, voice_channel)
        return guild.voice_client
    voice_channel.connect = connect
    panel = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    text_channel = SimpleNamespace(send=AsyncMock(return_value=panel))
    member = SimpleNamespace(id=5, display_name="小明", guild=guild, bot=False,
                             voice=SimpleNamespace(channel=voice_channel))
    voice_channel.members = [member]
    return guild, voice_channel, text_channel, member, panel


class CogTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot = SimpleNamespace(user=SimpleNamespace(id=999), loop=None, wait_until_ready=AsyncMock())
        self.cog = Music(bot)
        self.cog.ffmpeg = "ffmpeg"
        self.guild, self.voice_channel, self.text, self.member, self.panel = make_world()
        self.found = [track(1)]
        patches = [
            patch.object(music, "search", AsyncMock(side_effect=lambda *a, **k: list(self.found))),
            patch.object(music, "stream_url", AsyncMock(side_effect=lambda t: f"stream:{t.title}")),
            patch.object(music_cog.discord, "FFmpegOpusAudio", FakeSource),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    @property
    def voice(self) -> FakeVoice:
        return self.guild.voice_client

    async def test_must_be_in_voice_channel(self):
        self.member.voice = None
        with self.assertRaises(MusicError):
            await self.cog.request(self.member, self.text, "晴天")
        self.assertIsNone(self.voice)

    async def test_play_then_queue(self):
        note = await self.cog.request(self.member, self.text, "晴天")
        self.assertIn("開始播放", note)
        self.assertEqual(self.voice.sources[-1].url, "stream:歌1")
        self.text.send.assert_awaited()                    # 送出控制面板
        self.found = [track(2)]
        note = await self.cog.request(self.member, self.text, "稻香")
        self.assertIn("第 2 首", note)
        self.assertEqual(len(self.voice.sources), 1)       # 還在播第一首

    async def test_controls(self):
        self.found = [track(1), track(2), track(3)]
        await self.cog.request(self.member, self.text, "播放清單")
        await self.cog.control(self.guild, "next")
        self.assertEqual(self.voice.sources[-1].url, "stream:歌2")
        await self.cog.control(self.guild, "prev")
        self.assertEqual(self.voice.sources[-1].url, "stream:歌1")
        await self.cog.control(self.guild, "pause")
        self.assertTrue(self.voice.paused)
        await self.cog.control(self.guild, "resume")
        self.assertTrue(self.voice.playing)
        with self.assertRaises(MusicError):
            await self.cog.control(self.guild, "prev")     # 已經是第一首

    async def test_song_end_advances_and_old_callbacks_are_ignored(self):
        self.found = [track(1), track(2), track(3)]
        await self.cog.request(self.member, self.text, "播放清單")
        player = self.cog.players[1]
        old_generation = player.generation
        await self.cog.control(self.guild, "next")         # 跳到第 2 首
        await self.cog._track_ended(player, old_generation)  # 第 1 首被停掉的通知：忽略
        self.assertEqual(player.queue.index, 1)
        self.voice.playing = False
        await self.cog._track_ended(player, player.generation)  # 第 2 首播完
        self.assertEqual(self.voice.sources[-1].url, "stream:歌3")

    async def test_unplayable_song_is_skipped(self):
        self.found = [track(1), track(2)]
        music.stream_url.side_effect = [MusicError("影片不存在"), "stream:歌2"]
        await self.cog.request(self.member, self.text, "播放清單")
        self.assertEqual(self.voice.sources[-1].url, "stream:歌2")
        self.assertIn("跳過", self.text.send.await_args_list[0].args[0])

    async def test_stop_leaves(self):
        await self.cog.request(self.member, self.text, "晴天")
        await self.cog.control(self.guild, "stop")
        self.assertIsNone(self.voice)
        self.assertNotIn(1, self.cog.players)
        self.panel.edit.assert_awaited()

    async def test_busy_in_another_channel(self):
        await self.cog.request(self.member, self.text, "晴天")
        other = SimpleNamespace(name="另一個", permissions_for=lambda me: SimpleNamespace(connect=True, speak=True))
        friend = SimpleNamespace(id=6, display_name="小華", guild=self.guild, voice=SimpleNamespace(channel=other))
        with self.assertRaises(MusicError):
            await self.cog.request(friend, self.text, "稻香")

    async def test_panel_buttons_only_for_listeners(self):
        await self.cog.request(self.member, self.text, "晴天")
        view = self.cog.players[1].view
        stranger = SimpleNamespace(id=7, voice=None)
        interaction = SimpleNamespace(user=stranger, response=MagicMock(), followup=MagicMock())
        with patch.object(music_cog, "send_error", AsyncMock()) as error:
            self.assertFalse(await view.interaction_check(interaction))
            error.assert_awaited()
        self.assertTrue(await view.interaction_check(SimpleNamespace(user=self.member)))


if __name__ == "__main__":
    unittest.main()
