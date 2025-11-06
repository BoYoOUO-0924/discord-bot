import discord
from discord.ext import commands


class HelpCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='help', help='顯示所有功能與用法')
    async def help_command(self, ctx):
        prefix = '!'
        embed = discord.Embed(
            title='指令說明',
            description=f'目前前綴為 `{prefix}`。以下為可用功能與範例：',
            color=0x00bcd4
        )

        # 猜數字
        embed.add_field(
            name='猜數字 (Guess Number)',
            value=(
                f'• 開始/猜測：`{prefix}guess [1-100 數字]`\n'
                f'• 放棄：`{prefix}giveup`\n'
                f'說明：每個頻道各自一局。系統會提示高/低；猜中或放棄時會公布答案與累計猜測次數。\n'
                f'獎勵：5 次內答對 +100 分、10 次內 +50 分（超過 10 次 0 分）'
            ),
            inline=False
        )

        # 21點
        embed.add_field(
            name='Blackjack (21 點)',
            value=(
                f'• 開局：`{prefix}blackjack [賭注]`（你與莊家各 2 張，莊家亮 1 張）\n'
                f'• 要牌：`{prefix}hit`\n'
                f'• 停牌：`{prefix}stand`\n'
                f'• 分牌：可分時用按鈕或 `{prefix}split`\n'
                f'• 積分：查詢 `{prefix}point`，預設起始 {0} 分（JSON 本地保存）\n'
                f'說明：A 會自動在 1/11 間調整。玩家爆牌直接結算；莊家補到至少 17 再比較點數，可能平手。'
            ),
            inline=False
        )
        
        # 德州撲克
        embed.add_field(
            name='德州撲克 (Texas Hold\'em)',
            value=(
                f'• 建立房間：`{prefix}poker`\n'
                f'• 遊戲互動：透過按鈕【加入/離開/開始遊戲】\n'
                f'• 遊戲流程：開始後，透過按鈕【過牌/跟注/加注/棄牌】進行遊戲。\n'
                f'說明：一個更複雜的多人撲克遊戲。籌碼與 21點 連動。'
            ),
            inline=False
        )

        # 每日簽到
        embed.add_field(
            name='每日簽到 (Check-in)',
            value=(
                f'• 指令：`{prefix}checkin`\n'
                f'• 規則：基礎 +100，首次簽到額外 +500；連續簽到每日加成 +20（無上限）\n'
                f'• 日界：以 UTC 00:00 為換日基準'
            ),
            inline=False
        )

        # 井字遊戲 (UI互動版)
        embed.add_field(
            name='井字遊戲 (Tic-Tac-Toe) - UI互動版',
            value=(
                f'• 開始遊戲：`{prefix}tictactoe @對手` (別名: `ttt`, `井字遊戲`)\n'
                f'• 下棋：直接點擊遊戲盤面上的按鈕即可。\n'
                f'• 結束遊戲：`{prefix}stoptictactoe` (限管理員)\n'
                f'說明：透過互動式 UI 按鈕遊玩的 3x3 井字遊戲。'
            ),
            inline=False
        )

        # 海龜湯
        embed.add_field(
            name='🐢 海龜湯 (Sea Tortoise)',
            value=(
                f'• 開始遊戲：`{prefix}seatortoise` (別名: `海龜湯`)\n'
                f'• 提問：直接在頻道中提出「是/否」問題 (需以問號結尾)。\n'
                f'• 猜測答案：`{prefix}answer <你的猜測>`\n'
                f'• 放棄/看答案：`{prefix}giveup`\n'
                f'說明：由 AI 擔任湯主，玩家透過問答來推理故事真相的懸疑遊戲。'
            ),
            inline=False
        )

        embed.set_footer(text='有想加入的新遊戲，直接跟我說！')
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
