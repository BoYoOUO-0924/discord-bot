import discord
from discord.ext import commands
import os
import json
import random
import time
from typing import Dict, Optional, Any, Tuple
import google.generativeai as genai
import asyncio
from .ui.pet_views import PetDashboardView, FOOD_MENU

# --- AI Setup ---
try:
    import config
    GEMINI_API_KEY = getattr(config, "GEMINI_API_KEY", None)
except ImportError:
    GEMINI_API_KEY = None

if GEMINI_API_KEY and GEMINI_API_KEY != "PUT_YOUR_GEMINI_API_KEY_HERE":
    genai.configure(api_key=GEMINI_API_KEY)
    
    generation_config = {
        "temperature": 0.9,
        "max_output_tokens": 1024,
    }
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    model = genai.GenerativeModel(model_name="gemini-2.5-flash",
                                  generation_config=generation_config,
                                  safety_settings=safety_settings)
else:
    model = None

# --- Configuration ---
COG_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(COG_DIR)
DATA_FILE = os.path.join(PROJECT_ROOT, 'data', 'pet.json')
CONFIG_FILE = os.path.join(PROJECT_ROOT, 'configs', 'pet_types.json')
ASSETS_DIR = os.path.join(PROJECT_ROOT, 'assets', 'pets')
MAX_LEVEL = 100

class PetCog(commands.Cog):
    """Gawa-mon RPG System"""
    def __init__(self, bot):
        self.bot = bot
        self._ensure_data_file()
        self.pet_types = self._load_pet_config()

    async def generate_content_safe(self, prompt: str) -> str:
        """Safe wrapper for Gemini API"""
        if not model: return "錯誤：AI 模組未啟用"
        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(None, lambda: model.generate_content(prompt))
            return response.text.strip() if response.parts else "AI 無回應"
        except Exception as e:
            print(f"Gemini Error: {e}")
            return "AI 生成失敗"

    def _load_pet_config(self) -> Dict:
        """Load static pet configuration from JSON"""
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading pet config: {e}")
            return {}

    def _ensure_data_file(self):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        if not os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump({}, f)

    def _load_data(self) -> Dict:
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}

    def _save_data(self, data: Dict):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_pet(self, user_id: int) -> Optional[Dict]:
        data = self._load_data()
        return data.get(str(user_id))

    def _create_pet(self, user_id: int, p_type: str, name: str = None):
        base = self.pet_types[p_type]
        # Random IVs (-5% to +5%)
        iv_mult = random.uniform(0.95, 1.05)
        
        pet_data = {
            "type": p_type,
            "name": name or base["name"],
            "level": 1,
            "exp": 0,
            "stats": {
                "max_hp": int(base["base_stats"]["hp"] * iv_mult),
                "hp": int(base["base_stats"]["hp"] * iv_mult), # Current HP
                "atk": int(base["base_stats"]["atk"] * iv_mult),
                "def": int(base["base_stats"]["def"] * iv_mult),
                "satiety": 50, # Initial hunger
                "max_satiety": 100
            },
            "adopted_at": time.time(),
            "last_interaction": time.time(),
            "nickname": None
        }
        
        data = self._load_data()
        data[str(user_id)] = pet_data
        self._save_data(data)
        return pet_data

    def get_pet_embed(self, user_id: int) -> Tuple[Optional[discord.Embed], Optional[discord.File]]:
        """Helper to generate pet embed and file for dashboard updates"""
        pet = self._get_pet(user_id)
        if not pet: return None, None
        
        p_type = pet["type"]
        meta = self.pet_types.get(p_type)
        if not meta: return None, None

        imgPath = os.path.join(ASSETS_DIR, meta['image'])
        if not os.path.exists(imgPath):
            print(f"Warning: Image not found {imgPath}, using default.")
            imgPath = os.path.join(ASSETS_DIR, "嘎蛙寶寶.png")
            
        file = discord.File(imgPath, filename="pet.png")
        
        embed = discord.Embed(title=f"{meta['emoji']} {pet.get('nickname') or pet['name']} (Lv.{pet['level']})", color=meta['color'])
        if pet.get('nickname'):
            embed.description = f"種族: {pet['name']}"
        embed.set_thumbnail(url="attachment://pet.png")
        
        # Stats Bar visual
        hp_per = pet['stats']['hp'] / pet['stats']['max_hp']
        hp_bar = "🟩" * int(hp_per * 10) + "⬛" * (10 - int(hp_per * 10))
        
        sat_per = pet['stats'].get('satiety', 50) / pet['stats'].get('max_satiety', 100)
        sat_bar = "🍖" * int(sat_per * 10) + "⬛" * (10 - int(sat_per * 10))
        
        embed.add_field(name="體力 (HP)", value=f"{hp_bar} {pet['stats']['hp']}/{pet['stats']['max_hp']}", inline=False)
        embed.add_field(name="飽食 (Satiety)", value=f"{sat_bar} {pet['stats'].get('satiety', 50)}/{pet['stats'].get('max_satiety', 100)}", inline=False)
        embed.add_field(name="攻擊 (ATK)", value=f"{pet['stats']['atk']}", inline=True)
        embed.add_field(name="防禦 (DEF)", value=f"{pet['stats']['def']}", inline=True)
        
        if 'skills' in meta:
             embed.add_field(name="技能", value="\n".join([f"• {s}" for s in meta['skills']]), inline=True)
        else:
             embed.add_field(name="技能", value="無", inline=True)
        
        if pet['level'] >= MAX_LEVEL:
             exp_next = "MAX"
        else:
             exp_next = (pet['level'] ** 2) * 50
        embed.set_footer(text=f"經驗值: {pet['exp']}/{exp_next} | 屬性: {meta['element']}")
        
        return embed, file

    def train_pet(self, user_id: int) -> Dict[str, Any]:
        """Handles pet training logic: EXP cost, Level Up, Stat Growth"""
        pet = self._get_pet(user_id)
        if not pet: return {"status": "error", "msg": "沒有寵物"}
        
        # Check Stats
        if pet['stats']['hp'] < 10 or pet['stats'].get('satiety', 0) < 5:
            return {"status": "fail", "msg": "🚫 狀態不佳！請先餵食或休息。"}
            
        # Cost
        cost_satiety = 5
        buff_type = pet.get('buff')
        base_exp = random.randint(10, 20)
        gain_exp = base_exp * 2 if buff_type == "2x_exp" else base_exp
        cost_hp = 0 if buff_type == "invincible" else random.randint(5, 15)
        
        if buff_type: pet['buff'] = None
        
        # Apply Cost
        pet['stats']['hp'] -= cost_hp
        pet['stats']['satiety'] -= cost_satiety
        pet['exp'] += gain_exp
        
        # Level Up Check
        leveled_up = False
        evolution_ready = False
        msg_extra = ""
        
        # Exponential Curve: Level^2 * 50
        # Exponential Curve: Level^2 * 50
        while pet['level'] < MAX_LEVEL:
            req_exp = (pet['level'] ** 2) * 50
            if pet['exp'] >= req_exp:
                pet['exp'] -= req_exp
                pet['level'] += 1
                leveled_up = True
                
                # Stat Growth
                p_type = pet['type']
                growth = self.pet_types[p_type].get('growth_rate', {"hp": 10, "atk": 2, "def": 1})
                
                pet['stats']['max_hp'] += growth['hp']
                pet['stats']['hp'] = pet['stats']['max_hp'] # Full heal on Level Up
                pet['stats']['atk'] += growth['atk']
                pet['stats']['def'] += growth['def']
                
                msg_extra += f"\n🎊 **升級了！(Lv.{pet['level']})** (ATK+{growth['atk']} DEF+{growth['def']})"
            else:
                break
        
        if pet['level'] >= MAX_LEVEL:
             pet['exp'] = 0 # Cap EXP
                
        # Check Evolution
        p_type = pet['type']
        evo_data = self.pet_types[p_type].get('evolution')
        if evo_data and pet['level'] >= evo_data['min_level']:
            evolution_ready = True
            
        self._save_data({str(user_id): pet})
        
        return {
            "status": "success",
            "gain_exp": gain_exp,
            "cost_hp": cost_hp,
            "cost_satiety": cost_satiety,
            "leveled_up": leveled_up,
            "evolution_ready": evolution_ready,
            "msg_extra": msg_extra,
            "pet": pet
        }

    def evolve_pet(self, user_id: int) -> Dict[str, Any]:
        """Handles pet evolution logic"""
        data = self._load_data()
        pet = data.get(str(user_id))
        if not pet: return {"status": "error", "msg": "沒有寵物"}
        
        p_type = pet['type']
        meta = self.pet_types[p_type]
        evo_data = meta.get('evolution')
        
        if not evo_data:
            return {"status": "fail", "msg": "此寵物無法再進化或是條件未滿足"}
            
        if pet['level'] < evo_data['min_level']:
            return {"status": "fail", "msg": f"等級不足！需要 Lv.{evo_data['min_level']}"}
            
        next_form_id = evo_data['next_form']
        next_meta = self.pet_types.get(next_form_id)
        
        if not next_meta:
             return {"status": "error", "msg": "進化型態資料缺失"}
             
        # Apply Evolution
        # 1. Stat Boost (Difference between Base Stats)
        old_base = meta['base_stats']
        new_base = next_meta['base_stats']
        
        diff_hp = new_base['hp'] - old_base['hp']
        diff_atk = new_base['atk'] - old_base['atk']
        diff_def = new_base['def'] - old_base['def']
        
        pet['stats']['max_hp'] += diff_hp
        pet['stats']['hp'] = pet['stats']['max_hp'] # Full heal
        pet['stats']['atk'] += diff_atk
        pet['stats']['def'] += diff_def
        
        # 2. Change Type
        pet['type'] = next_form_id
        
        self._save_data(data)
        
        return {
            "status": "success",
            "msg": evo_data.get('msg', "進化成功！"),
            "new_name": next_meta['name'],
            "diff_hp": diff_hp,
            "diff_atk": diff_atk,
            "diff_def": diff_def
        }

    # --- Commands ---

    @commands.group(invoke_without_command=True)
    async def pet(self, ctx):
        """顯示你的嘎蛙狀態卡片"""
        pet = self._get_pet(ctx.author.id)
        if not pet:
            await ctx.send(f"{ctx.author.mention} 你還沒有領養嘎蛙喔！\n輸入 `!adopt` 來挑選你的夥伴！")
            return

        embed, file = self.get_pet_embed(ctx.author.id)
        if not embed:
             await ctx.send("系統錯誤：無法讀取寵物資料")
             return

        view = PetDashboardView(self, ctx.author.id)
        await ctx.send(file=file, embed=embed, view=view)

    @commands.command(name="adopt")
    async def adopt(self, ctx):
        """領養一隻嘎蛙 (三選一，AI 謎語版)"""
        if self._get_pet(ctx.author.id):
            await ctx.send("你已經有一隻嘎蛙了！不能太貪心喔。")
            return

        # 1. Pick 3 types
        choices = ['fire', 'water', 'forest']
        random.shuffle(choices) # Shuffle so Egg 1 is not always Fire
        
        # 2. Generate Riddles via AI
        info_str = ", ".join([f"選項{i+1}: {self.pet_types[k]['element']} ({self.pet_types[k]['name']})" for i, k in enumerate(choices)])
        
        prompt = f"""
        你是嘎蛙世界的守護者。現在有三顆寵物蛋，分別孕育著不同的屬性。
        請為這三顆蛋各寫一句「神秘的描述/謎語」，暗示它的屬性，但絕對「不要」直接說出屬性名稱或寵物名字。
        讓玩家產生好奇心去選擇。
        
        選項資訊: {info_str}
        
        請嚴格依照 JSON 格式回傳，格式如下：
        {{"1": "描述1", "2": "描述2", "3": "描述3"}}
        不要 Markdown，只要純 JSON。
        """
        
        waiting_msg = await ctx.send("🔮 正在感應蛋的能量 (AI 生成謎題中)...")
        
        riddle_json = await self.generate_content_safe(prompt)
        
        try:
            # Clean possible markdown ```json ... ```
            clean_json = riddle_json.replace("```json", "").replace("```", "").strip()
            riddles = json.loads(clean_json)
        except:
            # Fallback if AI fails
            riddles = {
                "1": "這顆蛋散發著神祕的光芒...",
                "2": "這顆蛋摸起來有點特別...",
                "3": "這顆蛋似乎在震動..."
            }
            
        await waiting_msg.delete()

        # 3. Create View
        view = discord.ui.View()
        
        async def callback(interaction: discord.Interaction, selected_idx: int):
            if interaction.user.id != ctx.author.id: return
            
            selected_type = choices[selected_idx]
            p_name = self.pet_types[selected_type]["name"]
            
            # Directly create pet using default name
            self._create_pet(interaction.user.id, selected_type, p_name)
            
            # Fetch created pet for display
            meta = self.pet_types[selected_type]
            imgPath = os.path.join(ASSETS_DIR, meta['image'])
            file = discord.File(imgPath, filename="new_pet.png")
            
            embed = discord.Embed(
                title=f"🎉 恭喜！蛋孵化了！是 {p_name}！", 
                description=f"謎底揭曉：**{meta['element']}**！\n{meta['emoji']} {meta['name']}\n好好照顧他吧！",
                color=meta['color']
            )
            embed.set_image(url="attachment://new_pet.png")
            await interaction.response.send_message(file=file, embed=embed)
            view.stop()

        # Create buttons
        desc_text = ""
        for i in range(3):
            rid = riddles.get(str(i+1), "神祕的氣息...")
            desc_text += f"**蛋 {i+1}**: {rid}\n\n"
            
            btn = discord.ui.Button(label=f"選擇蛋 {i+1}", style=discord.ButtonStyle.secondary, emoji="🥚")
            
            # Closure
            async def btn_callback(interaction, idx=i):
                await callback(interaction, idx)
                
            btn.callback = btn_callback
            view.add_item(btn)

        embed = discord.Embed(title="🥚 命運的抉擇", description=f"眼前有三顆蛋，請根據描述選擇你的夥伴：\n\n{desc_text}", color=0x9B59B6)
        await ctx.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(PetCog(bot))
