import discord
from discord.ext import commands
import os
import json
import random
import time
from datetime import datetime
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
SKILLS_FILE = os.path.join(PROJECT_ROOT, 'configs', 'skills.json')

class PetCog(commands.Cog):
    """Gawa-mon RPG System"""
    def __init__(self, bot):
        self.bot = bot
        self._ensure_data_file()
        self.pet_types = self._load_json(CONFIG_FILE)
        self.skills_data = self._load_json(SKILLS_FILE)

    def _load_json(self, filepath: str) -> Dict:
        """Load JSON config safely"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {filepath}: {e}")
            return {}

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
            "ap": 6, # Added
            "max_ap": 6, # Added
            "stats": {
                "max_hp": int(base["base_stats"]["hp"] * iv_mult),
                "hp": int(base["base_stats"]["hp"] * iv_mult), # Current HP
                "atk": int(base["base_stats"]["atk"] * iv_mult),
                "def": int(base["base_stats"]["def"] * iv_mult),
                "satiety": 100, # Changed from 50
                "max_satiety": 100
            },
            "skills": self.pet_types[p_type].get("learnset", {}).get("1", []), # Added
            "adopted_at": datetime.now().timestamp(), # Changed to datetime
            "last_interaction": datetime.now().timestamp(), # Changed to datetime
            "nickname": None,
            "buff": None # Added
        }
        
        data = self._load_data()
        data[str(user_id)] = pet_data
        self._save_data(data)
        return pet_data

    def _migrate_pet_data(self, pet: Dict) -> Dict:
        """Ensures pet data has all necessary fields for older saves."""
        if "ap" not in pet:
            pet["ap"] = 6
        if "max_ap" not in pet:
            pet["max_ap"] = 6
        if "skills" not in pet:
            pet["skills"] = []
        # Ensure skills is a list (migration fix)
        if isinstance(pet.get("skills"), str):
             pet["skills"] = []
        if "buff" not in pet: # Added buff migration
            pet["buff"] = None
        return pet

    def _get_pet(self, user_id: int) -> Optional[Dict]:
        data = self._load_data()
        pet = data.get(str(user_id))
        if pet:
            return self._migrate_pet_data(pet) # Apply migration when loading pet
        return None

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
        
        embed = discord.Embed(title=f"{meta['emoji']} {pet.get('nickname') or meta['name']} (Lv.{pet['level']})", color=meta['color'])
        if pet.get('nickname'):
            embed.description = f"種族: {meta['name']}"
        embed.set_thumbnail(url="attachment://pet.png")
        
        # Stats Bar visual
        hp_per = pet['stats']['hp'] / pet['stats']['max_hp']
        hp_bar = "🟩" * int(hp_per * 10) + "⬛" * (10 - int(hp_per * 10))
        
        sat_per = pet['stats'].get('satiety', 50) / pet['stats'].get('max_satiety', 100)
        sat_bar = "🍖" * int(sat_per * 10) + "⬛" * (10 - int(sat_per * 10))
        
        # AP Bar (Blue) - 1:1 mapping for 6 AP
        cur_ap = pet.get('ap', 0)
        max_ap = pet.get('max_ap', 6)
        ap_bar = "🟦" * cur_ap + "⬛" * (max_ap - cur_ap)
        
        embed.add_field(name="體力 (HP)", value=f"{hp_bar} {pet['stats']['hp']}/{pet['stats']['max_hp']}", inline=False)
        embed.add_field(name="飽食 (Sat)", value=f"{sat_bar} {pet['stats'].get('satiety', 50)}/{pet['stats'].get('max_satiety', 100)}", inline=False)
        
        # Stats Row (AP, ATK, DEF)
        embed.add_field(name="行動力 (AP)", value=f"{ap_bar} {cur_ap}/{max_ap}", inline=True)
        embed.add_field(name="攻擊 (ATK)", value=f"{pet['stats']['atk']}", inline=True)
        embed.add_field(name="防禦 (DEF)", value=f"{pet['stats']['def']}", inline=True)
        
        # Display skills
        if pet.get('skills'):
             embed.add_field(name="技能", value="\n".join([f"• {s}" for s in pet['skills']]), inline=True)
        else:
             embed.add_field(name="技能", value="無", inline=True)
        
        if pet['level'] >= MAX_LEVEL:
             exp_next = "MAX"
        else:
             exp_next = (pet['level'] ** 2) * 50
        embed.set_footer(text=f"經驗值: {pet['exp']}/{exp_next} | 屬性: {meta['element']}")
        
        return embed, file

    def _learn_skills(self, pet_data: Dict) -> list[str]:
        """Checks and learns new skills based on level."""
        pet_type = pet_data["type"]
        level = pet_data["level"]
        learnset = self.pet_types.get(pet_type, {}).get("learnset", {})
        
        learned_something = False
        new_skills = []

        # Check all levels up to current (in case of multi-level jump)
        for req_level_str, skills in learnset.items():
            req_level = int(req_level_str)
            if req_level <= level:
                for skill in skills:
                    if skill not in pet_data["skills"]:
                        pet_data["skills"].append(skill)
                        new_skills.append(skill)
                        learned_something = True
        
        return new_skills

    async def train_pet(self, user_id: int) -> Tuple[Optional[Dict], Optional[str]]:
        """Handles pet training logic: EXP cost, Level Up, Stat Growth"""
        pet = self._get_pet(user_id)
        if not pet:
            return None, "你要先領養一隻寵物！輸入 `!adopt` 開始。"

        # Check HP
        if pet["stats"]["hp"] <= 10:
             return None, "你的寵物體力透支了！請先休息 (`!rest`) 或餵食 (`!feed`)。"
        
        # Check Satiety
        if pet["stats"]["satiety"] <= 5:
             return None, "你的寵物肚子餓扁了！請先餵食 (`!feed`)。"
             
        # Check AP (New)
        if pet.get("ap", 0) < 1:
             return None, "你的寵物累了(AP不足)！請休息 (`!rest`) 來恢復體力。"

        # EXP Calculation (Exponential Curve)
        req_exp = (pet['level'] ** 2) * 50
        
        # Base EXP Gain
        gain_exp = random.randint(15, 25)
        
        # Apply Buff (if any)
        buff_msg = ""
        if pet.get("buff") == "2x_exp":
            gain_exp *= 2
            pet["buff"] = None # consume buff
            buff_msg = " (雙倍經驗生效！)"

        pet['exp'] += gain_exp
        pet['stats']['hp'] -= 10
        pet['stats']['satiety'] -= 5
        pet['ap'] -= 1 # Consume AP
        
        # Level Up Logic
        leveled_up = False
        level_msg = ""
        
        while pet['exp'] >= req_exp:
            if pet['level'] >= MAX_LEVEL:
                pet['exp'] = req_exp # Cap at max
                break
                
            pet['exp'] -= req_exp
            pet['level'] += 1
            leveled_up = True
            
            # Stat Growth
            p_type = self.pet_types.get(pet['type'])
            growth = p_type.get('growth_rate', {'hp': 5, 'atk': 2, 'def': 1})
            
            pet['stats']['max_hp'] += growth['hp']
            pet['stats']['hp'] = pet['stats']['max_hp'] # Full heal on level up
            pet['stats']['atk'] += growth['atk']
            pet['stats']['def'] += growth['def']
            pet['ap'] = pet['max_ap'] # Restore AP on level up
            
            # Recalculate next req_exp for the loop
            req_exp = (pet['level'] ** 2) * 50

            # Learn Skills
            new_skills = self._learn_skills(pet)
            if new_skills:
                level_msg += f"\n💡 領悟了新技能：{'、'.join(new_skills)}！"

            # Check Evolution
            if 'evolution' in p_type and p_type['evolution']:
                evo_data = p_type['evolution']
                if pet['level'] >= evo_data['min_level']:
                    # Just notify, user needs to click button
                    level_msg += f"\n✨ **寵物可以進化了！** 請點擊下方的進化按鈕！"

        # Load full data to prevent overwriting other users
        data = self._load_data()
        data[str(user_id)] = pet
        self._save_data(data)
        
        msg = f"特訓完成！獲得 {gain_exp} 經驗值{buff_msg}。"
        if leveled_up:
            msg += f"\n🎉 **升級了！目前等級 Lv.{pet['level']}**！{level_msg}"
            
        return pet, msg

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
