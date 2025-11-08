
import discord
from discord.ext import commands
from discord import ui # 引入 ui 模組
import random
import asyncio
import traceback

# --- 互動視圖 (View) ---
class SlotsView(ui.View):
    def __init__(self, cog, original_author, bet):
        super().__init__(timeout=180.0)  # 按鈕在 3 分鐘後會自動失效
        self.cog = cog
        self.original_author = original_author
        self.bet = bet
        self.message = None

    @ui.button(label="再轉一次", style=discord.ButtonStyle.primary, emoji="🔄")
    async def spin_again_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.original_author.id:
            await interaction.response.send_message("這不是你的拉霸機！", ephemeral=True)
            return

        await interaction.response.defer()

        points_cog = self.cog.bot.get_cog('Points')
        if not points_cog:
            await interaction.followup.send("❌ **系統錯誤**：積分模組目前無法使用。", ephemeral=True)
            return
        
        current_points = points_cog.get_points(self.original_author.id)
        if current_points < self.bet:
            button.disabled = True
            await interaction.message.edit(view=self)
            await interaction.followup.send("💸 **你的積分不足！** 無法再轉一次。", ephemeral=True)
            return

        await self.cog._play_spin(interaction.message, self.original_author, self.bet, points_cog)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass # 訊息可能已被刪除，忽略即可

class SlotsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.symbols = ['💎', '7️⃣', '⭐', '🍀', '🔔', '🍇', '🍒', '🍋']
        self.weights = [2, 4, 6, 8, 10, 15, 25, 30]
        self.payouts = {
            '💎💎💎': 100,
            '7️⃣7️⃣7️⃣': 50,
            '⭐⭐⭐': 25,
            '🍀🍀🍀': 15,
            '🔔🔔🔔': 10,
            '🍇🍇🍇': 7,
            '🍒🍒🍒': 5,
            '🍋🍋🍋': 3,
        }

    async def _play_spin(self, message: discord.Message, author: discord.Member, bet: int, points_cog):
        points_cog.update_points(author.id, -bet)

        embed = discord.Embed(title="[ 🎰 拉霸機 ]", color=discord.Color.blue())
        embed.set_author(name=f"{author.display_name} 下注了 {bet} 分", icon_url=author.avatar.url if author.avatar else None)
        embed.description = "### [ ❓ | ❓ | ❓ ]\n*滾輪轉動中...*"
        
        try:
            await message.edit(embed=embed, view=None)
        except discord.NotFound:
            # 如果原始訊息被刪，就重新發送一條
            message = await author.send(embed=embed)

        await asyncio.sleep(0.5)
        for i in range(3):
            reels = random.choices(self.symbols, k=3)
            embed.description = f"### [ {reels[0]} | {reels[1]} | {reels[2]} ]\n*滾輪轉動中... ({i+1}/3)*"
            await message.edit(embed=embed)
            await asyncio.sleep(0.6)

        final_reels = random.choices(self.symbols, weights=self.weights, k=3)
        result_key = "".join(final_reels)
        payout_multiplier = 0
        win_message = ""

        if result_key in self.payouts:
            payout_multiplier = self.payouts[result_key]
            win_message = f"中了三個 {final_reels[0]}！贏得了 {payout_multiplier} 倍獎金！"
            if payout_multiplier >= 50: win_message = f"🎉🎉🎉 JACKPOT! {win_message} 🎉🎉🎉"
        elif final_reels.count('🍒') == 2:
            payout_multiplier = 2
            win_message = "中了兩顆櫻桃！不錯喔！"
        elif final_reels.count('🍒') == 1:
            payout_multiplier = 1
            win_message = "一顆櫻桃！返還賭注！"

        winnings = int(bet * payout_multiplier)

        if winnings > 0:
            points_cog.update_points(author.id, winnings)
        
        new_total = points_cog.get_points(author.id)
        net_change = winnings - bet

        if winnings > 0:
            embed.color = discord.Color.gold()
            embed.description = f"### [ {final_reels[0]} | {final_reels[1]} | {final_reels[2]} ]\n**{win_message}**"
        else:
            embed.color = discord.Color.dark_grey()
            embed.description = f"### [ {final_reels[0]} | {final_reels[1]} | {final_reels[2]} ]\n**可惜，這次沒中。再接再厲！**"

        embed.set_footer(text=f"賭注: {bet} / 淨損益: {net_change:+} | 目前積分: {new_total}")

        view = SlotsView(self, author, bet)
        await message.edit(embed=embed, view=view)
        view.message = message

    @commands.command(name="slots", aliases=["拉霸機"])
    async def slots(self, ctx: commands.Context, bet: int):
        points_cog = self.bot.get_cog('Points')
        if not points_cog: return await ctx.send("❌ **系統錯誤**：積分模組未載入。")

        if bet <= 0: return await ctx.send("🚫 **賭注必須是正數！**")

        current_points = points_cog.get_points(ctx.author.id)
        if current_points < bet: return await ctx.send(f"💸 **你的積分不足！** 你目前只有 **{current_points}** 分。")
        
        slot_message = await ctx.send("準備開始...")
        await self._play_spin(slot_message, ctx.author, bet, points_cog)

    # --- 關鍵改造：將錯誤處理器變成智慧型說明書 ---
    @slots.error
    async def slots_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            # 當使用者輸入 !slots 而沒有帶參數時，發送說明書
            embed = discord.Embed(
                title="🎰 拉霸機 (Slots) 指令說明",
                description="體驗刺激的拉霸機遊戲，用你的積分贏得大獎！",
                color=discord.Color.dark_blue()
            )
            if self.bot.user.avatar:
                 embed.set_author(name=self.bot.user.name, icon_url=self.bot.user.avatar.url)
            
            embed.add_field(
                name="➡️ 如何遊玩",
                value="```\n!slots [賭注金額]\n```\n**別名:** `!拉霸機`\n**範例:** `!slots 10`",
                inline=False
            )
            
            embed.add_field(
                name="🔄 互動功能：再轉一次",
                value="遊戲結束後，訊息下方會出現「再轉一次」按鈕。\n"
                      "點擊即可用**相同的賭注**立即開始新的一局！\n"
                      "*(按鈕僅對發起遊戲的玩家有效，3分鐘後自動失效)*",
                inline=False
            )
            
            payout_table = ""
            for combo, multi in self.payouts.items():
                payout_table += f"{combo} : **{multi} 倍**\n"
            payout_table += "🍒 (任意位置 x2) : **2 倍**\n"
            payout_table += "🍒 (任意位置 x1) : **1 倍 (回本)**\n"

            embed.add_field(
                name="💰 獎金賠率表",
                value=payout_table,
                inline=False
            )
            embed.set_footer(text="祝你好運！ Good Luck!")

            await ctx.send(embed=embed)

        elif isinstance(error, commands.BadArgument):
            await ctx.send("**參數錯誤！** ➡️ `!slots [賭注]`\n您的賭注必須是一個有效的數字。")
        else:
            error_details = traceback.format_exc()
            print(f"--- Slots Command Unexpected Error ---\n{error_details}-------------------------------------")
            await ctx.send(f"🚨 拉霸機發生未預期錯誤，請查看主控台紀錄。錯誤: `{type(error).__name__}`")

async def setup(bot: commands.Bot):
    await bot.add_cog(SlotsCog(bot))
