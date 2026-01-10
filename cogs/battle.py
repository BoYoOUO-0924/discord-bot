import discord
from discord.ext import commands
import random
import asyncio
import os
import json
from .ui.battle_views import ChallengeView, PVPBattleView, BattleSkillView
from .battle_logic import calculate_damage, get_effectiveness_msg, ELEMENT_MAP

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

    @commands.command(name="adventure")
    async def adventure(self, ctx):
        """開始單人冒險 (PVE)"""
        pet_cog = self.bot.get_cog("PetCog")
        if not pet_cog: return await ctx.send("寵物系統維護中。")

        p1_pet = pet_cog._get_pet(ctx.author.id)
        if not p1_pet:
            return await ctx.send("你還沒有領養寵物！輸入 `!adopt` 開始。")

        # Calculate Wild Level
        level = p1_pet['level']
        wild_level = max(1, level + random.randint(-2, 2))
        
        # Start Battle with CPU
        try:
            await self.start_battle(ctx, ctx.author.id, "cpu", wild_level=wild_level)
        except Exception as e:
            import traceback
            traceback.print_exc()
            await ctx.send(f"冒險模式啟動失敗: {e}")

    async def start_battle(self, interaction, p1_id, p2_id, wild_level=None):
        battle_id = self.battle_counter
        self.battle_counter += 1
        
        pet_cog = self.bot.get_cog("PetCog")
        p1_pet = pet_cog._get_pet(p1_id)
        
        if p2_id == "cpu":
             # Generate Wild Pet
             p2_pet = pet_cog.generate_wild_pet(wild_level or 1)
             p2_name = "野生怪物"
             p1_user = interaction.author if isinstance(interaction, discord.ext.commands.Context) else interaction.user
             p1_name = p1_user.display_name
        else:
             p2_pet = pet_cog._get_pet(p2_id)
             # Fetch User Names
             p1_user = self.bot.get_user(p1_id)
             if not p1_user:
                 try:
                     p1_user = await self.bot.fetch_user(p1_id)
                 except: pass
             p1_name = p1_user.display_name if p1_user else f"User({p1_id})"
             p2_name = interaction.user.display_name

        # 初始化戰鬥狀態快照
        state = {
            "id": battle_id,
            "players": {
                p1_id: {"name": p1_name, "pet": p1_pet, "hp": p1_pet['stats']['hp'], "max_hp": p1_pet['stats']['max_hp'], "ap": 6, "status_effects": []},
                p2_id: {"name": p2_name, "pet": p2_pet, "hp": p2_pet['stats']['hp'], "max_hp": p2_pet['stats']['max_hp'], "ap": 6, "status_effects": []} 
            },
            "turn_order": [p1_id, p2_id],
            "turn_index": 0 if random.random() < 0.5 else 1, # 擲硬幣決定先攻
            "log": ["📢 戰鬥開始！擲硬幣決定先攻..."]
        }
        
        # 名稱已設定

        first_player = state['turn_order'][state['turn_index']]
        state["log"].append(f"👉 **{state['players'][first_player]['name']}** 獲得先攻！")
        
        if isinstance(interaction, commands.Context):
             # 如果是 Context (冒險模式)，發送新訊息作為戰鬥板
             message = await interaction.send("⚔️ **遭遇野生怪物！** 戰鬥載入中...")
             state['message'] = message
             self.battles[battle_id] = state
             # Context 沒有 interaction.response，所以直接更新訊息
             await self._update_battle_ui(interaction, battle_id)
        else:
             # 如果是 Interaction (PVP 接受挑戰)
             # 儲存訊息物件以便更新
             state['message'] = interaction.message
             self.battles[battle_id] = state
             await self._update_battle_ui(interaction, battle_id)

        # Trigger AI if it's CPU's turn (Coin flip result)
        first_player = state['turn_order'][state['turn_index']]
        if first_player == "cpu":
             self.bot.loop.create_task(self._ai_turn(interaction, battle_id))

    async def _update_battle_ui(self, interaction, battle_id, animation_url=None):
        battle = self.battles.get(battle_id)
        if not battle: return

        p1_id, p2_id = battle['turn_order']
        p1 = battle['players'][p1_id]
        p2 = battle['players'][p2_id]
        
        # 血條顯示輔助函式
        def get_bar(cur, max_val, length=10):
            pct = cur / max_val
            emoji = "🟩"
            if pct <= 0.2: emoji = "🟥"
            elif pct <= 0.5: emoji = "🟨"
            
            return emoji * int(pct * length) + "⬛" * (length - int(pct * length))

        # ANSI Color Logs
        # Wraps logs in ansi code block
        formatted_logs = []
        for log in battle['log'][-5:]:
            # Remove Markdown Bold
            log = log.replace("**", "")
            
            # Simple keyword highlighting (Fragile but works for now)
            if "造成" in log and "點傷害" in log:
                 # Highlight damage in Red
                 # \u001b is the Escape character required for ANSI
                 log = log.replace("造成", "\u001b[0;31m造成").replace("點傷害", "點傷害\u001b[0m")
            formatted_logs.append(log)

        desc = "**戰鬥紀錄**\n```ansi\n" + "\n".join(formatted_logs) + "\n```" # Show last 5 logs
        
        embed = discord.Embed(title="⚔️ 嘎蛙大戰 (PVP)", description=desc, color=0xF39C12)
        
        if animation_url:
            embed.set_image(url=animation_url)
        
        # Player 1 Field
        embed.add_field(name=f"🔴 {p1['name']} ({p1['pet']['name']})", 
                        value=f"HP: {get_bar(p1['hp'], p1['max_hp'])} {p1['hp']}/{p1['max_hp']}\nAP: **{p1['ap']}** {'🟦'*p1['ap']}", inline=True)
        
        embed.add_field(name="VS", value="⚡", inline=True)

        # Player 2 Field
        embed.add_field(name=f"🔵 {p2['name']} ({p2['pet']['name']})", 
                        value=f"HP: {get_bar(p2['hp'], p2['max_hp'])} {p2['hp']}/{p2['max_hp']}\nAP: **{p2['ap']}** {'🟦'*p2['ap']}", inline=True)

        current_player = battle['turn_order'][battle['turn_index']]
        embed.set_footer(text=f"現在是 {battle['players'][current_player]['name']} 的回合")

        # Determine if Buttons should be disabled
        # In PVE, player is always p1. 
        # If current_player is 'cpu', view buttons should be disabled?
        # The view itself checks interaction.user.id.
        # But visually, we can maybe disable them.
        # Currently PVPBattleView just renders.
        
        view = PVPBattleView(self, battle_id)
        
        # 檢查是否為主要互動 (決定是否使用 response.edit_message)
        is_main_interaction = False
        
        # 如果是 Context (adventure)，interaction 就是 Context，沒有 response
        if isinstance(interaction, commands.Context):
             # 直接編輯訊息
             if battle.get('message'):
                 await battle['message'].edit(content=None, embed=embed, view=view)
             return # 結束

        if hasattr(interaction, 'message') and interaction.message:
            if battle.get('message') and interaction.message.id == battle['message'].id:
                is_main_interaction = True
        
        if is_main_interaction:
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        else:
            # External interaction (e.g. Skill Menu)
            # Edit the main message directly
            if battle.get('message'):
                await battle['message'].edit(content=None, embed=embed, view=view)
            
            # Acknowledge the ephemeral interaction
            if not interaction.response.is_done():
                await interaction.response.send_message("✅ 技能施放成功！", ephemeral=True)

    async def handle_action(self, interaction, battle_id, action_type):
        battle = self.battles.get(battle_id)
        if not battle: return

        attacker_id = interaction.user.id
        attacker = battle['players'][attacker_id] # Should validation be here?
        
        # Determine Defender
        defender_id = [pid for pid in battle['turn_order'] if pid != attacker_id][0]
        defender = battle['players'][defender_id]

        if action_type == "attack":
            # 獲取屬性 Key (Basic Attack 視為物理攻擊，使用寵物屬性)
            atk_elem_key = ELEMENT_MAP.get(attacker['pet']['element'], 'normal')
            def_elem_key = ELEMENT_MAP.get(defender['pet']['element'], 'normal')
            
            # 使用新的計算邏輯
            dmg, effectiveness = calculate_damage(attacker, defender, power=50, 
                                                        category="physical", 
                                                        atk_element=atk_elem_key,
                                                        def_element=def_elem_key)
            
            eff_msg = get_effectiveness_msg(effectiveness)
            
            defender['hp'] = max(0, defender['hp'] - dmg)
            battle['log'].append(f"⚔️ **{attacker['name']}** 攻擊了！ \u001b[0;31m造成 **{dmg}** 點傷害\u001b[0m {eff_msg}！")
            
            if defender['hp'] <= 0:
                return await self.end_battle(interaction, battle_id, winner_id=attacker_id)


        await self._next_turn(interaction, battle_id)


    # _calculate_damage and _get_effectiveness_msg removed (moved to battle_logic.py)

    def _apply_status_effect(self, battle, target_id, effect_data):
        target = battle['players'][target_id]
        
        status_id = effect_data.get('status_id')
        effect_type = effect_data.get('type') # status, buff, debuff
        
        # 建立名稱和 ID
        if effect_type == 'status':
            effect_id = status_id
            if status_id == 'burn': name = "🔥 燒傷"
            elif status_id == 'regen': name = "🛡️ 再生"
            else: name = status_id
        else:
            # Buff/Debuff
            stat = effect_data.get('stat')
            effect_id = f"{effect_type}_{stat}" # e.g. buff_def
            stat_map = {"atk": "攻擊", "def": "防禦", "spd": "速度"}
            sign = "提升" if effect_type == 'buff' else "下降"
            name = f"{stat_map.get(stat, stat)}{sign}"
            
        # 檢查是否已存在 (刷新持續時間)
        existing = next((e for e in target['status_effects'] if e['id'] == effect_id), None)
        
        if existing:
            existing['duration'] = effect_data['duration'] # 刷新回合數
            return f"🔄 **{target['name']}** 的 **{name}** 狀態持續時間刷新了！"
        else:
            new_effect = {
                "id": effect_id,
                "type": effect_type,
                "duration": effect_data['duration'],
                "value": effect_data.get('value', 0),
                "name": name
            }
            if effect_type in ['buff', 'debuff']:
                new_effect['stat'] = effect_data.get('stat')
            
            target['status_effects'].append(new_effect)
            return f"⚠️ **{target['name']}** 獲得了 **{name}** 狀態！"

    def _process_status_effects(self, battle, player_id):
        player = battle['players'][player_id]
        logs = []
        
        remaining_effects = []
        for effect in player['status_effects']:

            # 燒傷 (Burn)
            if effect['id'] == 'burn':
                dmg = int(player['max_hp'] * (effect['value'] / 100))
                dmg = max(1, dmg)
                player['hp'] = max(0, player['hp'] - dmg)
                logs.append(f"🔥 **{player['name']}** 受到燒傷傷害 \u001b[0;31m-{dmg}\u001b[0m！")
            
            # 再生 (Regen/Heal)
            elif effect['id'] == 'regen':
                 heal = int(effect['value'])
                 old_hp = player['hp']
                 player['hp'] = min(player['max_hp'], player['hp'] + heal)
                 actual_heal = player['hp'] - old_hp
                 if actual_heal > 0:
                     logs.append(f"💧 **{player['name']}** 回復了 \u001b[0;32m+{actual_heal}\u001b[0m 點 HP！")

            effect['duration'] -= 1
            if effect['duration'] > 0:
                remaining_effects.append(effect)
            else:
                logs.append(f"✨ **{player['name']}** 的 **{effect['name']}** 狀態結束了。")
        
        player['status_effects'] = remaining_effects
        return logs

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
        try:
            battle = self.battles.get(battle_id)
            if not battle: return
    
            attacker_id = interaction.user.id
            attacker = battle['players'][attacker_id]
            
            # 決定防守方
            defender_id = [pid for pid in battle['turn_order'] if pid != attacker_id][0]
            defender = battle['players'][defender_id]
    
            skill_data = self.skills_db.get(skill_name)
            if not skill_data:
                 return await interaction.response.send_message("技能資料錯誤！", ephemeral=True)
                 
            # 檢查 AP (Action Points)
            cost = skill_data['cost']
            if attacker['ap'] < cost:
                 return await interaction.response.send_message(f"AP 不足！需要 {cost} AP。", ephemeral=True)
    
            # 消耗 AP
            attacker['ap'] -= cost
            
            # 計算傷害
            power = skill_data['power']
            
            if skill_data.get('category') == 'status':
                 dmg = 0
                 msg = f"✨ **{attacker['name']}** 使用了 **{skill_name}**！"
            else:
                 atk_elem_key = skill_data.get('element', 'normal')
                 def_elem_key = ELEMENT_MAP.get(defender['pet'].get('element', '一般'), 'normal')
                 
                 dmg, effectiveness = calculate_damage(attacker, defender, power, 
                                                             category=skill_data.get('category', 'magic'),
                                                             atk_element=atk_elem_key,
                                                             def_element=def_elem_key)
                 
                 eff_msg = get_effectiveness_msg(effectiveness)
                 
                 defender['hp'] = max(0, defender['hp'] - dmg)
                 msg = f"🔮 **{attacker['name']}** 使用了 **{skill_name}**！ \u001b[0;31m造成 **{dmg}** 點傷害\u001b[0m {eff_msg}！"
    
            battle['log'].append(msg)
            
            # 狀態效果應用
            if 'effects' in skill_data:
                for effect in skill_data['effects']:
                    # 機率檢定
                    if random.randint(1, 100) <= effect['chance']:
                        target_id = defender_id if effect['target'] == 'enemy' else attacker_id
                        effect_log = self._apply_status_effect(battle, target_id, effect)
                        battle['log'].append(effect_log)
    
            if defender['hp'] <= 0:
                return await self.end_battle(interaction, battle_id, winner_id=attacker_id)
    
            # 獲取動畫 URL
            anim_url = skill_data.get('image_url')
    
            # 進入下一回合
            await self._next_turn(interaction, battle_id, animation_url=anim_url)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                await interaction.response.send_message(f"技能施放發生錯誤: {e}", ephemeral=True)
            except: pass


    async def end_battle(self, interaction, battle_id, winner_id):
        battle = self.battles.pop(battle_id, None)
        if not battle: return

        loser_id = [pid for pid in battle['turn_order'] if pid != winner_id][0]
        winner = battle['players'][winner_id]
        loser = battle['players'][loser_id]

        # 儲存戰鬥結果
        pet_cog = self.bot.get_cog("PetCog")
        if pet_cog:
            data = pet_cog._load_data()
            
            # 更新獲勝者資料
            w_pet = data.get(str(winner_id))
            if w_pet:
                if loser_id == "cpu":
                     # PVE 獎勵 (單人冒險)
                     pve_exp = loser['pet']['level'] * 20
                     w_pet['exp'] += pve_exp
                else:
                     w_pet['exp'] += 20
                     
                if w_pet['exp'] >= (w_pet['level']**2)*50 and w_pet['level'] < 100:
                    w_pet['exp'] -= (w_pet['level']**2)*50
                    w_pet['level'] += 1
                    # 簡易數值成長 (升級)
                    w_pet['stats']['max_hp'] += 5
                    w_pet['stats']['hp'] = w_pet['stats']['max_hp']
                    w_pet['stats']['atk'] += 2
                    w_pet['stats']['def'] += 1
                    w_pet['ap'] = 6

            # 更新落敗者資料
            if loser_id != "cpu":
                l_pet = data.get(str(loser_id))
                if l_pet:
                    l_pet['exp'] += 5
                    # l_pet['stats']['hp'] = 1 # 使用者要求無懲罰
            
            pet_cog._save_data(data)
            
        desc = f"🎉 勝利者: **{winner['name']}** (+{20 if loser_id != 'cpu' else winner['pet']['level']*20} EXP)\n💀 落敗者: {loser['name']}"
        if loser_id != "cpu":
             desc += " (+5 EXP)"
             
        embed = discord.Embed(title="🏆 戰鬥結束！", description=desc, color=0xFFD700)
        embed.add_field(name="戰利品", value="戰鬥資料已儲存！")
        
        # 決定是否將結果更新到訊息中
        # 理想情況下，我們總是更新主要訊息以公開戰報
        if battle.get('message'):
            try:
                await battle['message'].edit(content=None, embed=embed, view=None)
            except discord.NotFound:
                # 如果原始訊息被刪除的備案
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=embed, view=None)
        
        # 如果觸發結束的互動是暫時性訊息 (例如：技能選單)
        # 我們只需結束它，或提示使用者查看戰報
        if not interaction.response.is_done():
            # 如果是主互動 (例如：投降)，上面的 edit 可能已經處理完了
            pass  
            
        # 如果互動不是來自主要訊息更新 (例如：技能)，我們需要關閉那個暫時性回應
        if hasattr(interaction, 'message') and battle.get('message') and interaction.message.id != battle['message'].id:
             if not interaction.response.is_done():
                  await interaction.response.send_message("🏆 戰鬥結束！請查看公共戰報。", ephemeral=True)

    async def _next_turn(self, interaction, battle_id, animation_url=None):
        battle = self.battles.get(battle_id)
        if not battle: return
        
        # Rotates turn
        battle['turn_index'] = (battle['turn_index'] + 1) % 2
        
        # Start of Turn Processing
        current_player_id = battle['turn_order'][battle['turn_index']]
        
        # AP Recovery (Recover 2 AP, max 6)
        current_player_data = battle['players'][current_player_id]
        if current_player_data['ap'] < 6:
             current_player_data['ap'] = min(6, current_player_data['ap'] + 2)
        
        # Process Status Effects
        effect_logs = self._process_status_effects(battle, current_player_id)
        if effect_logs:
             battle['log'].extend(effect_logs)
             
        # Check if died from status
        if current_player_data['hp'] <= 0:
             winner_id = battle['turn_order'][(battle['turn_index'] + 1) % 2]
             return await self.end_battle(interaction, battle_id, winner_id)

        await self._update_battle_ui(interaction, battle_id, animation_url=animation_url)
        
        # AI Logic Triggers
        if current_player_id == "cpu":
             self.bot.loop.create_task(self._ai_turn(interaction, battle_id))

    async def _ai_turn(self, interaction, battle_id):
        """AI 決策邏輯"""
        await asyncio.sleep(1.5) # 模擬思考時間
        
        battle = self.battles.get(battle_id)
        if not battle: return
        
        ai_id = "cpu"
        ai_data = battle['players'][ai_id]
        
        # 尋找對手
        opponent_id = [pid for pid in battle['turn_order'] if pid != ai_id][0]
        opponent = battle['players'][opponent_id]
        
        # 決策邏輯
        skills = ai_data['pet'].get('skills', [])
        available_skills = []
        
        for s_name in skills:
             s_data = self.skills_db.get(s_name)
             if s_data and ai_data['ap'] >= s_data.get('cost', 1):
                  available_skills.append(s_data)
                  
        action = "attack"
        chosen_skill = None
        
        # 70% 機率使用技能 (如果有的話)
        if available_skills and random.random() < 0.7:
             action = "skill"
             chosen_skill = random.choice(available_skills)
             
        if action == "skill" and chosen_skill:
             s_name = chosen_skill['name']
             cost = chosen_skill['cost']
             ai_data['ap'] -= cost
             
             power = chosen_skill['power']
             category = chosen_skill.get('category', 'magic')
             
             if category == 'status':
                  dmg = 0
                  msg = f"🤖 **野生怪物** 使用了 **{s_name}**！"
             else:
                  atk_elem = chosen_skill.get('element', 'normal')
                  def_elem = ELEMENT_MAP.get(opponent['pet'].get('element', '一般'), 'normal')
                  
                  dmg, eff = calculate_damage(ai_data, opponent, power, category, atk_elem, def_elem)
                  eff_msg = get_effectiveness_msg(eff)
                  
                  opponent['hp'] = max(0, opponent['hp'] - dmg)
                  msg = f"🤖 **野生怪物** 使用了 **{s_name}**！ \u001b[0;31m造成 **{dmg}** 點傷害\u001b[0m {eff_msg}！"
             
             battle['log'].append(msg)
             anim_url = chosen_skill.get('image_url')
             
             # Status Effects (AI)
             if 'effects' in chosen_skill:
                  for effect in chosen_skill['effects']:
                       if random.randint(1, 100) <= effect['chance']:
                            target_id = opponent_id if effect['target'] == 'enemy' else ai_id
                            eff_log = self._apply_status_effect(battle, target_id, effect)
                            battle['log'].append(eff_log)

        else:
             # 普通攻擊
             atk_elem = ELEMENT_MAP.get(ai_data['pet'].get('element', '一般'), 'normal')
             def_elem = ELEMENT_MAP.get(opponent['pet'].get('element', '一般'), 'normal')
             
             dmg, eff = calculate_damage(ai_data, opponent, 50, "physical", atk_elem, def_elem)
             eff_msg = get_effectiveness_msg(eff)
             
             opponent['hp'] = max(0, opponent['hp'] - dmg)
             battle['log'].append(f"⚔️ **野生怪物** 攻擊了！ \u001b[0;31m造成 **{dmg}** 點傷害\u001b[0m {eff_msg}！")
             anim_url = None

        if opponent['hp'] <= 0:
             return await self.end_battle(interaction, battle_id, winner_id=ai_id)
             
        await self._next_turn(interaction, battle_id, animation_url=anim_url)

async def setup(bot):
    await bot.add_cog(BattleCog(bot))
