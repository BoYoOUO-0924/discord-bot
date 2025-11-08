import discord
from discord.ext import commands
import os
import json
from typing import Dict

# --- Absolute Path Definition ---
COG_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(COG_DIR)
POINTS_FILE_PATH = os.path.join(PROJECT_ROOT, 'data', 'points.json')

class PointsCog(commands.Cog, name="Points"): # Assign a public name for easy access
    """一個集中管理所有使用者積分的 Cog。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.points_path = POINTS_FILE_PATH
        self.STARTING_POINTS = 0
        # Ensure the data file and directory exist at startup
        self._ensure_data_file_exists()

    # --- Private I/O Methods ---
    def _ensure_data_file_exists(self):
        """確保 data 資料夾與 points.json 檔案存在。"""
        os.makedirs(os.path.dirname(self.points_path), exist_ok=True)
        if not os.path.exists(self.points_path):
            with open(self.points_path, 'w', encoding='utf-8') as f:
                json.dump({}, f)

    def _load_points(self) -> Dict[str, int]:
        """從 points.json 讀取所有積分資料。"""
        try:
            with open(self.points_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_points(self, all_points: Dict[str, int]):
        """將所有積分資料寫回 points.json。"""
        try:
            with open(self.points_path, 'w', encoding='utf-8') as f:
                json.dump(all_points, f, indent=4)
        except IOError as e:
            # In a real-world scenario, you might want to log this error
            print(f"寫入積分檔案時發生錯誤: {e}")

    # --- Public API for other Cogs ---
    def get_points(self, user_id: int) -> int:
        """給其他 Cog 使用的公開方法，用於安全地獲取單一使用者的積分。"""
        all_points = self._load_points()
        return all_points.get(str(user_id), self.STARTING_POINTS)

    def update_points(self, user_id: int, amount: int) -> int:
        """給其他 Cog 使用的公開方法，用於更新單一使用者的積分（可為負數）。"""
        user_id_str = str(user_id)
        all_points = self._load_points()
        current_points = all_points.get(user_id_str, self.STARTING_POINTS)
        new_points = current_points + amount
        all_points[user_id_str] = new_points
        self._save_points(all_points)
        return new_points

    # --- User-facing Command ---
    @commands.command(name='point', help='查看你目前的積分')
    async def point(self, ctx: commands.Context):
        """顯示指令使用者的目前積分。"""
        current_points = self.get_points(ctx.author.id)
        await ctx.send(f"{ctx.author.mention} 目前積分：{current_points}")


async def setup(bot: commands.Bot):
    await bot.add_cog(PointsCog(bot))
