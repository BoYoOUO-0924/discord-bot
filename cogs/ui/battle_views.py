import discord
import random

class ChallengeView(discord.ui.View):
    def __init__(self, cog, challenger_id, target_id):
        super().__init__(timeout=60)
        self.cog = cog
        self.challenger_id = challenger_id
        self.target_id = target_id
        self.accepted = False

    @discord.ui.button(label="接受挑戰", style=discord.ButtonStyle.success, emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            return await interaction.response.send_message("這不是給你的挑戰書！", ephemeral=True)
        
        self.accepted = True
        self.stop()
        await self.cog.start_battle(interaction, self.challenger_id, self.target_id)

    @discord.ui.button(label="拒絕", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            return await interaction.response.send_message("這不是給你的挑戰書！", ephemeral=True)
            
        await interaction.response.edit_message(content="❌ 對方拒絕了挑戰。", view=None)
        self.stop()

class BattleSkillSelect(discord.ui.Select):
    def __init__(self, cog, battle_id, skills_list):
        self.cog = cog
        self.battle_id = battle_id
        options = []
        
        # Load skill data to get details
        skill_db = cog.skills_db
        
        for s_name in skills_list:
            s_data = skill_db.get(s_name)
            if s_data:
                # Format: "🔥 Ember (Power:40 | AP:1)"
                label = f"{s_name}"
                desc = f"威力:{s_data['power']} | AP:{s_data['cost']} | {s_data['description'][:20]}"
                emoji = "🔮" if s_data['category'] == 'magic' else "👊"
                if s_data['category'] == 'status': emoji = "✨"
                
                options.append(discord.SelectOption(label=label, value=s_name, description=desc, emoji=emoji))
        
        if not options:
            options.append(discord.SelectOption(label="無技能", value="none", description="你還沒有學會任何技能"))

        super().__init__(placeholder="🔥 選擇要使用的技能...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        skill_name = self.values[0]
        if skill_name == "none":
            return await interaction.response.send_message("你沒有技能！", ephemeral=True)
            
        await self.cog.execute_skill(interaction, self.battle_id, skill_name)

class BattleSkillView(discord.ui.View):
    def __init__(self, cog, battle_id, skills_list):
        super().__init__(timeout=60)
        self.add_item(BattleSkillSelect(cog, battle_id, skills_list))

class PVPBattleView(discord.ui.View):
    def __init__(self, cog, battle_id):
        super().__init__(timeout=300) # 5 min battle timeout
        self.cog = cog
        self.battle_id = battle_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = self.cog.battles.get(self.battle_id)
        if not battle:
            await interaction.response.send_message("戰鬥已結束。", ephemeral=True)
            return False
            
        current_turn_player = battle['turn_order'][battle['turn_index']]
        
        if interaction.user.id != current_turn_player:
            await interaction.response.send_message("現在不是你的回合！", ephemeral=True)
            return False
        return True



    @discord.ui.button(label="技能", style=discord.ButtonStyle.primary, emoji="📚")
    async def skill(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Open Skill Selection Menu (Ephemeral)
        await self.cog.handle_skill_menu(interaction, self.battle_id)

    @discord.ui.button(label="認輸", style=discord.ButtonStyle.secondary, emoji="🏳️")
    async def surrender(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_surrender(interaction, self.battle_id)
