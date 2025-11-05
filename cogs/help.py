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

        embed.set_footer(text='有想加入的新遊戲，直接跟我說！')
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))


