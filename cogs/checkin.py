import discord
from discord.ext import commands
import os
import json
from datetime import datetime, timezone, timedelta


class CheckinCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        # 根目錄
        root_dir = os.path.dirname(os.path.dirname(__file__))
        # 積分檔與簽到檔
        self.points_path = os.path.join(root_dir, 'points.json')
        self.checkin_path = os.path.join(root_dir, 'checkin.json')
        self.STARTING_POINTS = 0
        # 載入資料
        self.user_points = self._load_json(self.points_path, default={})
        # { userId(str): { last_checkin_iso: str, consecutive_days: int, first_bonus_received: bool } }
        self.user_checkin = self._load_json(self.checkin_path, default={})

    @commands.command(name='checkin', help='每日簽到：基礎 +100，首次額外 +500，連續每日 +20 遞增（UTC 00:00換日）')
    async def checkin(self, ctx):
        user_id = str(ctx.author.id)
        now = datetime.now(timezone.utc)

        # 初始化使用者資料
        if user_id not in self.user_points:
            self.user_points[user_id] = self.STARTING_POINTS
            self._save_json(self.points_path, self.user_points)
        if user_id not in self.user_checkin:
            self.user_checkin[user_id] = {
                'last_checkin_iso': None,
                'consecutive_days': 0,
                'first_bonus_received': False,
            }

        record = self.user_checkin[user_id]
        last_iso = record.get('last_checkin_iso')
        last_dt = datetime.fromisoformat(last_iso) if last_iso else None

        # 以 UTC 日界判定是否已簽到過
        if last_dt and last_dt.date() == now.date():
            await ctx.send('你今天已經簽到過了。')
            return

        # 判斷連續簽到：昨天有簽到 -> 連續 +1；否則重置為 1
        if last_dt and (now.date() - last_dt.date()).days == 1:
            record['consecutive_days'] = int(record.get('consecutive_days', 0)) + 1
        else:
            record['consecutive_days'] = 1

        base = 100
        first_bonus = 0 if record.get('first_bonus_received') else 500
        # 連續簽到加成：第2天起，每日 +20（無上限）
        consecutive_bonus = 20 * max(0, record['consecutive_days'] - 1)

        gained = base + first_bonus + consecutive_bonus

        # 更新狀態
        record['last_checkin_iso'] = now.isoformat()
        if not record.get('first_bonus_received'):
            record['first_bonus_received'] = True

        # 更新積分（共用 points.json，與 Blackjack 一致）
        self.user_points[user_id] = int(self.user_points.get(user_id, self.STARTING_POINTS)) + gained

        # 保存（合併磁碟上現有 points，避免覆寫其他模組更新）
        self._save_json(self.checkin_path, self.user_checkin)
        on_disk = self._load_json(self.points_path, default={})
        on_disk.update(self.user_points)
        self._save_json(self.points_path, on_disk)

        await ctx.send(
            f"{ctx.author.mention} 簽到成功！本次獲得：{gained} 分\n"
            f"（基礎 +{base}、{'首次 +500、' if first_bonus else ''}連續 {record['consecutive_days']} 天，加成 +{consecutive_bonus}）\n"
            f"目前積分：{self.user_points[user_id]} "
        )

    # ------- I/O -------
    def _load_json(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return default.copy() if isinstance(default, dict) else default

    def _save_json(self, path, data):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(CheckinCog(bot))


