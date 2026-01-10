import random

# 屬性相剋表
# 攻擊方 -> 防守方 -> 傷害倍率
TYPE_CHART = {
    "fire": {"forest": 1.5, "water": 0.5, "fire": 0.5},
    "water": {"fire": 1.5, "forest": 0.5, "water": 0.5},
    "forest": {"water": 1.5, "fire": 0.5, "forest": 0.5},
    "ghost": {"ghost": 2.0, "normal": 0.0},
    "normal": {"ghost": 0.0}
}

# 中文屬性名稱對照表
ELEMENT_MAP = {
    "火": "fire",
    "水": "water",
    "草": "forest",
    "幽靈": "ghost",
    "一般": "normal"
}

def get_effectiveness_msg(value):
    """
    根據屬性相剋倍率回傳提示訊息 (含 ANSI 顏色碼)
    """
    if value > 1.0: return "\u001b[0;33m(效果絕佳!)\u001b[0m" # 黃色
    if value == 0: return "\u001b[1;30m(沒有效果...)\u001b[0m" # 灰色
    if value < 1.0: return "\u001b[0;34m(效果不好...)\u001b[0m" # 藍色
    return ""

def calculate_damage(attacker, defender, power, category="physical", atk_element='normal', def_element='normal'):
    """
    計算戰鬥傷害
    
    參數:
        attacker (dict): 攻擊方資料
        defender (dict): 防守方資料
        power (int): 技能威力
        category (str): 技能類型 (physical/magic/status)
        atk_element (str): 攻擊屬性 key
        def_element (str): 防守屬性 key
        
    回傳:
        (int, float): (最終傷害值, 屬性相剋倍率)
    """
    # 1. 基礎數值
    atk = attacker['pet']['stats']['atk']
    defense = defender['pet']['stats']['def']
    
    # 2. 計算 Buff/Debuff 加成
    # 攻擊方攻擊力修正
    atk_mod = sum(e['value'] for e in attacker['status_effects'] if e['type'] in ['buff'] and e['stat'] == 'atk')
    atk_mod -= sum(e['value'] for e in attacker['status_effects'] if e['type'] in ['debuff'] and e['stat'] == 'atk')
    
    # 防守方防禦力修正
    def_mod = sum(e['value'] for e in defender['status_effects'] if e['type'] in ['buff'] and e['stat'] == 'def')
    def_mod -= sum(e['value'] for e in defender['status_effects'] if e['type'] in ['debuff'] and e['stat'] == 'def')
    
    final_atk = max(1, atk + atk_mod)
    final_def = max(1, defense + def_mod)
    
    # 3. 傷害公式
    # 邏輯: (攻擊力 * 威力% * 2) - (防禦力 * 0.2)
    # 這裡的公式比較簡單，未來可以加入等級參數
    dmg = int( (final_atk * power / 100) * 2 )
    dmg = max(1, dmg - int(final_def * 0.2))
    
    # 4. 屬性相剋修正
    effectiveness = TYPE_CHART.get(atk_element, {}).get(def_element, 1.0)
    dmg = int(dmg * effectiveness)
    
    return dmg, effectiveness
