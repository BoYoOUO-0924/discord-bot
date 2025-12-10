import discord
from discord.ext import commands
import random

# --- Food Data ---
FOOD_MENU = {
    "1": {"name": "早餐店奶茶", "price": 20, "heal": 20, "satiety": 10, "buff": None},
    "2": {"name": "小李便當", "price": 100, "heal": 100, "satiety": 40, "buff": None},
    "3": {"name": "越南河粉", "price": 120, "heal": 30, "satiety": 80, "buff": "invincible"}, # High Satiety
    "4": {"name": "韓式炸雞", "price": 250, "heal": 999, "satiety": 50, "buff": "2x_exp"} # Buff: Next Train 2x EXP
}

class RenameModal(discord.ui.Modal, title='幫嘎蛙取新名字'):
    name = discord.ui.TextInput(label='新名字', placeholder='例如：呱呱', required=True, max_length=10)

    def __init__(self, cog, user_id):
        super().__init__()
        self.cog = cog
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        data = self.cog._load_data()
        pet = data.get(str(self.user_id))
        
        if not pet:
            await interaction.response.send_message("找不到你的嘎蛙！", ephemeral=True)
            return
            
        new_name = self.name.value
        pet['nickname'] = new_name
        data[str(self.user_id)] = pet
        self.cog._save_data(data)
        
        embed, file = self.cog.get_pet_embed(self.user_id)
        
        await interaction.response.edit_message(content=f"✅ 改名成功！現在他是 **{new_name}** 了！", embed=embed, attachments=[file])

class FeedSelect(discord.ui.Select):
    def __init__(self, cog, user_id):
        self.cog = cog
        self.user_id = user_id
        options = []
        for pid, item in FOOD_MENU.items():
            desc = f"${item['price']} | ❤️+{item['heal']} 🍖+{item['satiety']}"
            if item['buff']: desc += " [BUFF]"
            options.append(discord.SelectOption(label=item['name'], value=pid, description=desc, emoji="🍱"))
            
        super().__init__(placeholder="🍽️ 選擇食物餵食...", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id: return
        item_id = self.values[0]
        
        item = FOOD_MENU.get(item_id)
        points_cog = self.cog.bot.get_cog("Points")
        if not points_cog: return await interaction.response.send_message("積分系統維護中", ephemeral=True)
        
        user_points = points_cog.get_points(self.user_id)
        if user_points < item['price']:
            return await interaction.response.send_message(f"💸 積分不足！(需 ${item['price']})", ephemeral=True)

        data = self.cog._load_data()
        pet = data.get(str(self.user_id))
        
        if not pet: return await interaction.response.send_message("沒有寵物！", ephemeral=True)
        
        if pet['stats']['hp'] >= pet['stats']['max_hp'] and pet['stats'].get('satiety',0) >= 100:
             return await interaction.response.send_message("🤢 吃太飽了！", ephemeral=True)

        points_cog.update_points(self.user_id, -item['price'])
        
        # Heal HP & Satiety
        old_hp = pet['stats']['hp']
        heal = item['heal']
        if heal >= 999: pet['stats']['hp'] = pet['stats']['max_hp']
        else: pet['stats']['hp'] = min(pet['stats']['max_hp'], old_hp + heal)
        
        old_sat = pet['stats'].get('satiety', 50)
        max_sat = pet['stats'].get('max_satiety', 100)
        pet['stats']['satiety'] = min(max_sat, old_sat + item['satiety'])
        
        actual_heal = pet['stats']['hp'] - old_hp
        actual_sat = pet['stats']['satiety'] - old_sat

        if item['buff']: pet['buff'] = item['buff']
        
        self.cog._save_data(data)
        
        embed, file = self.cog.get_pet_embed(self.user_id)
        msg = f"😋 吃了 **{item['name']}**！\n(HP +{actual_heal} | 飽食 +{actual_sat})"
        await interaction.response.edit_message(content=msg, embed=embed, attachments=[file], view=self.view)

class EvolveButton(discord.ui.Button):
    def __init__(self, cog, user_id):
        super().__init__(label="✨ 進化", style=discord.ButtonStyle.primary, row=2)
        self.cog = cog
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id: return
        
        res = self.cog.evolve_pet(self.user_id)
        if res['status'] == 'fail':
             return await interaction.response.send_message(res['msg'], ephemeral=True)
        
        # Success
        embed, file = self.cog.get_pet_embed(self.user_id)
        self.view.remove_item(self) # Remove button after use
        await interaction.response.edit_message(content=f"🎆 **{res['msg']}**\n(HP+{res['diff_hp']} / ATK+{res['diff_atk']} / DEF+{res['diff_def']})", embed=embed, attachments=[file], view=self.view)

class PetDashboardView(discord.ui.View):
    def __init__(self, cog, user_id):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self.add_item(FeedSelect(cog, user_id))
        
        # Check Evolution
        pet = self.cog._get_pet(user_id)
        if pet:
             p_type = pet['type']
             # Important: Ensure config exists (might be missing if config file changed but bot didn't reload config fully? No, cog loads config on init)
             meta = self.cog.pet_types.get(p_type, {})
             evo_data = meta.get('evolution')
             if evo_data and pet['level'] >= evo_data['min_level']:
                  self.add_item(EvolveButton(cog, user_id))
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("這不是你的介面！", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="特訓", style=discord.ButtonStyle.danger, emoji="⚔️", row=0)
    async def train_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        res = self.cog.train_pet(self.user_id)
        
        if res['status'] == 'fail':
             return await interaction.response.send_message(res['msg'], ephemeral=True)
             
        if res['status'] == 'error':
             return await interaction.response.send_message("❌ 系統錯誤", ephemeral=True)

        # Dynamic Button Update
        if res['evolution_ready']:
             # Check if button exists
             if not any(isinstance(x, EvolveButton) for x in self.children):
                  self.add_item(EvolveButton(self.cog, self.user_id))

        embed, file = self.cog.get_pet_embed(self.user_id)
        msg = f"⚔️ 特訓完成！EXP +{res['gain_exp']} / HP -{res['cost_hp']}{res['msg_extra']}"
        await interaction.response.edit_message(content=msg, embed=embed, attachments=[file], view=self)

    @discord.ui.button(label="休息", style=discord.ButtonStyle.success, emoji="💤", row=0)
    async def rest_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog._load_data()
        pet = data.get(str(self.user_id))
        if not pet: return

        if pet['stats'].get('satiety', 0) < 30:
            return await interaction.response.send_message("🚫 太餓了！需要 30 飽食度。", ephemeral=True)
        if pet['stats']['hp'] >= pet['stats']['max_hp']:
            return await interaction.response.send_message("💤 精神很好不用睡。", ephemeral=True)
            
        pet['stats']['satiety'] -= 30
        old_hp = pet['stats']['hp']
        pet['stats']['hp'] = min(pet['stats']['max_hp'], old_hp + 60)
        
        self.cog._save_data(data)
        embed, file = self.cog.get_pet_embed(self.user_id)
        msg = f"💤 休息好了！HP +{pet['stats']['hp']-old_hp} / 飽食 -30"
        await interaction.response.edit_message(content=msg, embed=embed, attachments=[file], view=self)

    @discord.ui.button(label="技能", style=discord.ButtonStyle.primary, emoji="📚", row=0)
    async def skills_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = self.cog._load_data()
        pet = data.get(str(self.user_id))
        meta = self.cog.pet_types[pet['type']]
        await interaction.response.send_message(f"📚 **{pet['name']} 的技能**:\n" + "\n".join(meta['skills']), ephemeral=True)

    @discord.ui.button(label="改名", style=discord.ButtonStyle.secondary, emoji="✏️", row=0)
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RenameModal(self.cog, self.user_id))
