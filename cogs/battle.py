import discord
from discord.ext import commands
import random
import asyncio
import os
import json
from .ui.battle_views import ChallengeView, PVPBattleView, BattleSkillView



PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_FILE = os.path.join(PROJECT_ROOT, 'configs', 'skills.json')

class BattleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.battles = {} # battle_id -> state
        self.battle_counter = 0
        self.skills_db = self._load_json(SKILLS_FILE)

    def _load_json(self, filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}

    @commands.command(name="battle")
    async def battle(self, ctx, target: discord.Member):
        """發起 PVP 挑戰"""
        if target.bot or target.id == ctx.author.id:
            return await ctx.send("你不能挑戰自己或機器人！")

        pet_cog = self.bot.get_cog("PetCog")
        if not pet_cog: return await ctx.send("寵物系統維護中。")

        p1_pet = pet_cog._get_pet(ctx.author.id)
        p2_pet = pet_cog._get_pet(target.id)

        if not p1_pet: return await ctx.send("你還沒有領養寵物！")
        if not p2_pet: return await ctx.send(f"{target.display_name} 還沒有領養寵物！")

        # Send Challenge
        embed = discord.Embed(title="⚔️ PVP 挑戰書", description=f"{ctx.author.mention} 向 {target.mention} 發起了挑戰！\n雙方準備好了嗎？", color=0xFF0000)
        view = ChallengeView(self, ctx.author.id, target.id)
        await ctx.send(embed=embed, view=view)

    async def start_battle(self, interaction, p1_id, p2_id):
        battle_id = self.battle_counter
        self.battle_counter += 1
        
        pet_cog = self.bot.get_cog("PetCog")
        p1_pet = pet_cog._get_pet(p1_id)
        p2_pet = pet_cog._get_pet(p2_id)

        # Fetch User Names
        p1_user = self.bot.get_user(p1_id)
        if not p1_user:
            try:
                p1_user = await self.bot.fetch_user(p1_id)
            except:
                pass
        
        p1_name = p1_user.display_name if p1_user else f"User({p1_id})"
        p2_name = interaction.user.display_name

        # Snapshot State
        state = {
            "id": battle_id,
            "players": {
                p1_id: {"name": p1_name, "pet": p1_pet, "hp": p1_pet['stats']['hp'], "max_hp": p1_pet['stats']['max_hp'], "ap": 6},
                p2_id: {"name": p2_name, "pet": p2_pet, "hp": p2_pet['stats']['hp'], "max_hp": p2_pet['stats']['max_hp'], "ap": 6} 
            },
            "turn_order": [p1_id, p2_id],
            "turn_index": 0 if random.random() < 0.5 else 1, # Coin Flip
            "log": ["📢 戰鬥開始！擲硬幣決定先攻..."]
        }
        
        # Names already set in state


        first_player = state['turn_order'][state['turn_index']]
        state["log"].append(f"👉 **{state['players'][first_player]['name']}** 獲得先攻！")

        self.battles[battle_id] = state
        
        await self._update_battle_ui(interaction, battle_id)

    async def _update_battle_ui(self, interaction, battle_id):
        battle = self.battles.get(battle_id)
        if not battle: return

        p1_id, p2_id = battle['turn_order']
        p1 = battle['players'][p1_id]
        p2 = battle['players'][p2_id]
        
        # Helper for HP Bar
        def get_bar(cur, max_val, length=10):
            pct = cur / max_val
            return "🟩" * int(pct * length) + "⬛" * (length - int(pct * length))

        desc = "**戰鬥紀錄**\n" + "\n".join(battle['log'][-5:]) # Show last 5 logs
        
        embed = discord.Embed(title="⚔️ 嘎蛙大戰 (PVP)", description=desc, color=0xF39C12)
        
        # Player 1 Field
        embed.add_field(name=f"🔴 {p1['name']} ({p1['pet']['name']})", 
                        value=f"HP: {get_bar(p1['hp'], p1['max_hp'])} {p1['hp']}/{p1['max_hp']}\nAP: {'🟦'*p1['ap']}", inline=True)
        
        embed.add_field(name="VS", value="⚡", inline=True)

        # Player 2 Field
        embed.add_field(name=f"🔵 {p2['name']} ({p2['pet']['name']})", 
                        value=f"HP: {get_bar(p2['hp'], p2['max_hp'])} {p2['hp']}/{p2['max_hp']}\nAP: {'🟦'*p2['ap']}", inline=True)

        current_player = battle['turn_order'][battle['turn_index']]
        embed.set_footer(text=f"現在是 {battle['players'][current_player]['name']} 的回合")

        view = PVPBattleView(self, battle_id)
        
        if interaction.type == discord.InteractionType.component:
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view) # Should not happen often

    async def handle_action(self, interaction, battle_id, action_type):
        battle = self.battles.get(battle_id)
        if not battle: return
        
        attacker_id = interaction.user.id
        attacker = battle['players'][attacker_id]
        
        # Determine Defender
        defender_id = [pid for pid in battle['turn_order'] if pid != attacker_id][0]
        defender = battle['players'][defender_id]

        if action_type == "attack":
            dmg = int(attacker['pet']['stats']['atk'] * 0.5) # Simple formula
            dmg = max(1, dmg - int(defender['pet']['stats']['def'] * 0.1))
            
            defender['hp'] = max(0, defender['hp'] - dmg)
            battle['log'].append(f"⚔️ **{attacker['name']}** 攻擊了！造成 **{dmg}** 點傷害！")
            
            if defender['hp'] <= 0:
                return await self.end_battle(interaction, battle_id, winner_id=attacker_id)

        # Basic Turn Switch
        battle['turn_index'] = (battle['turn_index'] + 1) % 2
        
        # AP Restore for next player
        next_pid = battle['turn_order'][battle['turn_index']]
        battle['players'][next_pid]['ap'] = min(6, battle['players'][next_pid]['ap'] + 1)

        await self._update_battle_ui(interaction, battle_id)

    async def handle_surrender(self, interaction, battle_id):
        battle = self.battles.get(battle_id)
        winner_id = [pid for pid in battle['turn_order'] if pid != interaction.user.id][0]
        battle['log'].append(f"🏳️ **{battle['players'][interaction.user.id]['name']}** 認輸了！")
        await self.end_battle(interaction, battle_id, winner_id=winner_id)

    async def handle_skill_menu(self, interaction, battle_id):
        battle = self.battles.get(battle_id)
        if not battle: return
        
        user_id = interaction.user.id
        player = battle['players'][user_id]
        
        skills = player['pet'].get('skills', [])
        if not skills:
            return await interaction.response.send_message("你的寵物還沒有學會技能！", ephemeral=True)
            
        view = BattleSkillView(self, battle_id, skills)
        await interaction.response.send_message("選擇要使用的技能：", view=view, ephemeral=True)

    async def execute_skill(self, interaction, battle_id, skill_name):
        battle = self.battles.get(battle_id)
        if not battle: return

        attacker_id = interaction.user.id
        attacker = battle['players'][attacker_id]
        
        # Determine Defender
        defender_id = [pid for pid in battle['turn_order'] if pid != attacker_id][0]
        defender = battle['players'][defender_id]

        skill_data = self.skills_db.get(skill_name)
        if not skill_data:
             return await interaction.response.send_message("技能資料錯誤！", ephemeral=True)
             
        # Check AP
        cost = skill_data['cost']
        if attacker['ap'] < cost:
             return await interaction.response.send_message(f"AP 不足！需要 {cost} AP。", ephemeral=True)

        # Consume AP
        attacker['ap'] -= cost
        
        # Calculate Damage
        power = skill_data['power']
        
        if skill_data['category'] == 'status':
             dmg = 0
             msg = f"✨ **{attacker['name']}** 使用了 **{skill_name}**！\n(狀態效果尚未實裝)"
        else:
             # Simpler Calc
             dmg = int( (attacker['pet']['stats']['atk'] * power / 100) * 2 )
             dmg = max(1, dmg - int(defender['pet']['stats']['def'] * 0.2))
             
             defender['hp'] = max(0, defender['hp'] - dmg)
             msg = f"🔮 **{attacker['name']}** 使用了 **{skill_name}**！造成 **{dmg}** 點傷害！"

        battle['log'].append(msg)
        
        if defender['hp'] <= 0:
            return await self.end_battle(interaction, battle_id, winner_id=attacker_id)

        # Switch Turn
        battle['turn_index'] = (battle['turn_index'] + 1) % 2
        
        # Restore AP
        next_pid = battle['turn_order'][battle['turn_index']]
        battle['players'][next_pid]['ap'] = min(6, battle['players'][next_pid]['ap'] + 1)

        await self._update_battle_ui(interaction, battle_id)

    async def end_battle(self, interaction, battle_id, winner_id):
        battle = self.battles.pop(battle_id, None)
        if not battle: return

        loser_id = [pid for pid in battle['turn_order'] if pid != winner_id][0]
        winner = battle['players'][winner_id]
        loser = battle['players'][loser_id]

        # Save Results
        pet_cog = self.bot.get_cog("PetCog")
        if pet_cog:
            data = pet_cog._load_data()
            
            # Update Winner
            w_pet = data.get(str(winner_id))
            if w_pet:
                w_pet['exp'] += 20
                if w_pet['exp'] >= (w_pet['level']**2)*50 and w_pet['level'] < 100:
                    w_pet['exp'] -= (w_pet['level']**2)*50
                    w_pet['level'] += 1
                    # Simple level up stats
                    w_pet['stats']['max_hp'] += 5
                    w_pet['stats']['hp'] = w_pet['stats']['max_hp']
                    w_pet['stats']['atk'] += 2
                    w_pet['stats']['def'] += 1
                    w_pet['ap'] = 6

            # Update Loser
            l_pet = data.get(str(loser_id))
            if l_pet:
                l_pet['exp'] += 5
                # l_pet['stats']['hp'] = 1 # No penalty requested by user
            
            pet_cog._save_data(data)
            
        embed = discord.Embed(title="🏆 戰鬥結束！", description=f"🎉 勝利者: **{winner['name']}** (+20 EXP)\n💀 落敗者: {loser['name']} (+5 EXP)", color=0xFFD700)
        embed.add_field(name="戰利品", value="戰鬥資料已儲存！")
        
        await interaction.response.edit_message(embed=embed, view=None)

async def setup(bot):
    await bot.add_cog(BattleCog(bot))
