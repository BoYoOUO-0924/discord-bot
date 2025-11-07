import discord
from discord.ext import commands
import os
import json
from datetime import datetime, timezone, timedelta

class CheckinCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        # Define the path for check-in data, but not for points
        root_dir = os.path.dirname(os.path.dirname(__file__))
        data_dir = os.path.join(root_dir, 'data')
        self.checkin_path = os.path.join(data_dir, 'checkin.json')
        self.user_checkin = self._load_json(self.checkin_path, default={})

    @commands.Cog.listener()
    async def on_ready(self):
        # Ensure the Points Cog is available
        self.points_cog = self.bot.get_cog('Points')
        if not self.points_cog:
            print("Error: PointsCog not found. Make sure it is loaded.")

    @commands.command(name='checkin', help='每日簽到：基礎 +100，首次額外 +500，連續每日 +20 遞增（UTC 00:00換日）')
    async def checkin(self, ctx):
        if not self.points_cog:
            await ctx.send("積分系統目前無法使用，請聯絡管理員。")
            return

        user_id = str(ctx.author.id)
        user_id_int = ctx.author.id
        now = datetime.now(timezone.utc)

        # Initialize user check-in data if not present
        if user_id not in self.user_checkin:
            self.user_checkin[user_id] = {
                'last_checkin_iso': None,
                'consecutive_days': 0,
                'first_bonus_received': False,
            }

        record = self.user_checkin[user_id]
        last_iso = record.get('last_checkin_iso')
        last_dt = datetime.fromisoformat(last_iso) if last_iso else None

        if last_dt and last_dt.date() == now.date():
            await ctx.send('你今天已經簽到過了。')
            return

        if last_dt and (now.date() - last_dt.date()).days == 1:
            record['consecutive_days'] = int(record.get('consecutive_days', 0)) + 1
        else:
            record['consecutive_days'] = 1

        base = 100
        first_bonus = 0 if record.get('first_bonus_received') else 500
        consecutive_bonus = 20 * max(0, record['consecutive_days'] - 1)

        gained = base + first_bonus + consecutive_bonus

        # Update check-in status
        record['last_checkin_iso'] = now.isoformat()
        if not record.get('first_bonus_received'):
            record['first_bonus_received'] = True

        # CRITICAL: Update points using the centralized PointsCog
        new_total = self.points_cog.update_points(user_id_int, gained)

        # Save only the check-in data
        self._save_json(self.checkin_path, self.user_checkin)

        await ctx.send(
            f"{ctx.author.mention} 簽到成功！本次獲得：{gained} 分\n"
            f"（基礎 +{base}、{'首次 +500、' if first_bonus else ''}連續 {record['consecutive_days']} 天，加成 +{consecutive_bonus}）\n"
            f"目前積分：{new_total}"
        )

    # --- I/O for checkin.json ONLY ---
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
