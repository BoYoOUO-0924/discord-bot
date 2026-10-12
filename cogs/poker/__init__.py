from .cog import Poker


async def setup(bot):
    await bot.add_cog(Poker(bot))
