# cogs/blackjack.py
import discord
from discord.ext import commands
import random
import json
import os


def build_shuffled_deck():
    ranks = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
    suits = ['♠', '♥', '♦', '♣']
    deck = [(r, s) for s in suits for r in ranks] * 1  # 單副牌即可
    random.shuffle(deck)
    return deck


def hand_value(cards):
    value_map = {
        'A': 11, 'K': 10, 'Q': 10, 'J': 10,
        '10': 10, '9': 9, '8': 8, '7': 7, '6': 6, '5': 5, '4': 4, '3': 3, '2': 2
    }
    total = 0
    aces = 0
    for r, _ in cards:
        if r == 'A':
            aces += 1
        total += value_map[r]
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def render_cards(cards):
    return ' '.join([f"{r}{s}" for r, s in cards]) if cards else '(無)'


def card_point(rank: str) -> int:
    # 單張牌的點數（A 視作 11）
    value_map = {
        'A': 11, 'K': 10, 'Q': 10, 'J': 10,
        '10': 10, '9': 9, '8': 8, '7': 7, '6': 6, '5': 5, '4': 4, '3': 3, '2': 2
    }
    return value_map[rank]


class BlackjackView(discord.ui.View):

    def __init__(self, cog, channel_id, owner_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.channel_id = channel_id
        self.owner_id = owner_id
        # 初始化時依當前手牌狀態決定是否允許分牌
        self._update_split_button_state()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 限制只有開局者可操作按鈕
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('只有開局者可以操作本局按鈕。', ephemeral=True)
            return False
        return True

    async def disable_all(self, interaction: discord.Interaction):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(view=self)

    def _is_split_eligible(self) -> bool:
        table = self.cog.tables.get(self.channel_id)
        if not table:
            return False
        if len(table.get('hands', [])) != 1:
            return False
        hand = table['hands'][0]
        if len(hand) != 2:
            return False
        # 僅允許相同牌面（例：J 與 J、10 與 10）
        return hand[0][0] == hand[1][0]

    def _update_split_button_state(self):
        eligible = self._is_split_eligible()
        for child in self.children:
            if isinstance(child, discord.ui.Button) and str(child.label).lower().startswith('split'):
                child.disabled = not eligible

    @discord.ui.button(label='Hit 要牌', style=discord.ButtonStyle.primary)
    async def hit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        table = self.cog.tables.get(self.channel_id)
        if not table or table.get('finished'):
            await interaction.response.send_message('本局已結束或不存在，請用 `!blackjack` 重新開始。', ephemeral=True)
            return
        hand_idx = table.get('active_index', 0)
        table['hands'][hand_idx].append(table['deck'].pop())
        p_val = hand_value(table['hands'][hand_idx])
        if p_val > 21:
            table['hand_done'][hand_idx] = True
            # 若仍有下一手未完成，切換到下一手；否則進入結算
            next_idx = self.cog._next_active_hand_index(table)
            if next_idx is None:
                # 所有手都完成，進入結算
                content = self.cog._final_message_multi(table)
                for child in self.children:
                    if isinstance(child, discord.ui.Button):
                        child.disabled = True
                await interaction.response.edit_message(content=content, view=self)
                table['finished'] = True
                return
            else:
                table['active_index'] = next_idx
        # 更新狀態訊息（Embed）
        self._update_split_button_state()
        embed = self.cog._build_status_embed(table, reveal_dealer=False)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label='Stand 停牌', style=discord.ButtonStyle.secondary)
    async def stand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        table = self.cog.tables.get(self.channel_id)
        if not table or table.get('finished'):
            await interaction.response.send_message('本局已結束或不存在，請用 `!blackjack` 重新開始。', ephemeral=True)
            return
        hand_idx = table.get('active_index', 0)
        table['hand_done'][hand_idx] = True
        next_idx = self.cog._next_active_hand_index(table)
        if next_idx is None:
            # 進入結算流程（莊家補到 17）
            d_val = hand_value(table['dealer'])
            while d_val < 17 and len(table['deck']) > 0:
                table['dealer'].append(table['deck'].pop())
                d_val = hand_value(table['dealer'])
            embed = self.cog._build_final_embed(table)
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            await interaction.response.edit_message(embed=embed, view=self)
            table['finished'] = True
            return
        else:
            table['active_index'] = next_idx
            self._update_split_button_state()
            embed = self.cog._build_status_embed(table, reveal_dealer=False)
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label='Split 分牌', style=discord.ButtonStyle.success)
    async def split_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        table = self.cog.tables.get(self.channel_id)
        if not table or table.get('finished'):
            await interaction.response.send_message('本局已結束或不存在，請用 `!blackjack` 重新開始。', ephemeral=True)
            return
        # 僅允許在未分牌且正好兩張且點數相等時
        if len(table['hands']) != 1:
            await interaction.response.send_message('目前無法分牌（已分過或不在可分狀態）。', ephemeral=True)
            return
        hand = table['hands'][0]
        if len(hand) != 2 or hand[0][0] != hand[1][0]:
            await interaction.response.send_message('只有首兩張牌面相同時才能分牌（例如 JJ、QQ、KK、10 10）。', ephemeral=True)
            return
        card1, card2 = hand
        # 分成兩手，並各補一張
        new_hand1 = [card1, table['deck'].pop()]
        new_hand2 = [card2, table['deck'].pop()]
        table['hands'] = [new_hand1, new_hand2]
        table['hand_done'] = [False, False]
        table['active_index'] = 0
        # 分牌後不再允許再次分牌
        self._update_split_button_state()
        embed = self.cog._build_status_embed(table, reveal_dealer=False)
        await interaction.response.edit_message(embed=embed, view=self)


class BlackjackCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        # 每頻道一個牌局：
        # { channel_id: {
        #     'deck': list,
        #     'hands': list[list],     # 玩家可含多手（分牌）
        #     'hand_done': list[bool], # 對應每手是否已停牌/結束
        #     'active_index': int,     # 目前操作的手
        #     'dealer': list,
        #     'finished': bool,
        #     'owner_id': int
        # } }
        self.tables = {}
        self.STARTING_POINTS = 0
        # 設定保存路徑：專案根目錄下的 data/points.json
        root_dir = os.path.dirname(os.path.dirname(__file__))
        data_dir = os.path.join(root_dir, 'data')
        self.points_path = os.path.join(data_dir, 'points.json')
        self.user_points = self._load_points()

    @commands.command(name='blackjack', help='開始一局 21 點。可加上賭注：!blackjack 100')
    async def blackjack(self, ctx, bet: int = 0):
        channel_id = ctx.channel.id
        # 建新桌或覆蓋舊桌
        # 先重新讀取積分，避免其他模組（如簽到）剛更新而記憶體尚未同步
        self.user_points = self._load_points()
        deck = build_shuffled_deck()
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]
        owner_id = ctx.author.id

        if owner_id not in self.user_points:
            self.user_points[owner_id] = self.STARTING_POINTS
            self._save_points()

        if bet < 0:
            await ctx.send('賭注必須是非負整數。')
            return
        if bet > self.user_points.get(owner_id, 0):
            await ctx.send(f'你的積分不足。目前餘額：{self.user_points[owner_id]}')
            return
        self.tables[channel_id] = {
            'deck': deck,
            'hands': [player],
            'hand_done': [False],
            'active_index': 0,
            'dealer': dealer,
            'finished': False,
            'owner_id': owner_id,
            'bet': bet
        }

        p_val = hand_value(player)
        # 開局訊息（莊家亮一張）
        bet_txt = f"（賭注：{bet}）" if bet > 0 else ''
        embed = self._build_status_embed(self.tables[channel_id], reveal_dealer=False, footer_extra=bet_txt)

        # 開局即黑傑克
        if p_val == 21:
            d_val = hand_value(dealer)
            self.tables[channel_id]['finished'] = True
            result = self._decide_result(p_val, d_val)
            # 立即結算並用 Embed 顯示
            self._settle_and_format_single(self.tables[channel_id], player, dealer, p_val, d_val, result)
            final_embed = self._build_final_embed(self.tables[channel_id])
            # 黑傑克直接結束，按鈕失效
            view = BlackjackView(self, channel_id, ctx.author.id)
            for child in view.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            await ctx.send(embed=final_embed, view=view)
            return

        view = BlackjackView(self, channel_id, ctx.author.id)
        await ctx.send(embed=embed, view=view)

    @commands.command(name='hit', help='要牌')
    async def hit(self, ctx):
        channel_id = ctx.channel.id
        table = self.tables.get(channel_id)
        if not table or table.get('finished'):
            await ctx.send('目前沒有進行中的牌局，請先使用 `!blackjack` 開局。')
            return

        idx = table.get('active_index', 0)
        table['hands'][idx].append(table['deck'].pop())
        p_val = hand_value(table['hands'][idx])
        if p_val > 21:
            table['hand_done'][idx] = True
            next_idx = self._next_active_hand_index(table)
            if next_idx is None:
                # 結算（莊家補到 17）
                d_val = hand_value(table['dealer'])
                while d_val < 17 and len(table['deck']) > 0:
                    table['dealer'].append(table['deck'].pop())
                    d_val = hand_value(table['dealer'])
                await ctx.send(embed=self._build_final_embed(table))
                table['finished'] = True
                return
            else:
                table['active_index'] = next_idx

        await ctx.send(embed=self._build_status_embed(table, reveal_dealer=False))

    @commands.command(name='stand', help='停牌並讓莊家補牌')
    async def stand(self, ctx):
        channel_id = ctx.channel.id
        table = self.tables.get(channel_id)
        if not table or table.get('finished'):
            await ctx.send('目前沒有進行中的牌局，請先使用 `!blackjack` 開局。')
            return

        idx = table.get('active_index', 0)
        table['hand_done'][idx] = True
        next_idx = self._next_active_hand_index(table)
        if next_idx is None:
            # 莊家補到至少 17
            d_val = hand_value(table['dealer'])
            while d_val < 17 and len(table['deck']) > 0:
                table['dealer'].append(table['deck'].pop())
                d_val = hand_value(table['dealer'])
            table['finished'] = True
            await ctx.send(embed=self._build_final_embed(table))
            return
        else:
            table['active_index'] = next_idx
            await ctx.send(embed=self._build_status_embed(table, reveal_dealer=False))

    @commands.command(name='split', help='在首兩張相同點數時分牌')
    async def split(self, ctx):
        channel_id = ctx.channel.id
        table = self.tables.get(channel_id)
        if not table or table.get('finished'):
            await ctx.send('目前沒有進行中的牌局，請先使用 `!blackjack` 開局。')
            return
        if len(table['hands']) != 1:
            await ctx.send('目前無法分牌（已分過或不在可分狀態）。')
            return
        hand = table['hands'][0]
        if len(hand) != 2 or card_point(hand[0][0]) != card_point(hand[1][0]):
            await ctx.send('只有首兩張「點數」相同時才能分牌（例如 10 與 K）。')
            return
        card1, card2 = hand
        new_hand1 = [card1, table['deck'].pop()]
        new_hand2 = [card2, table['deck'].pop()]
        table['hands'] = [new_hand1, new_hand2]
        table['hand_done'] = [False, False]
        table['active_index'] = 0
        await ctx.send(embed=self._build_status_embed(table, reveal_dealer=False))

    def _decide_result(self, player_val, dealer_val):
        if player_val > 21:
            return 'lose'
        if dealer_val > 21:
            return 'win'
        if player_val > dealer_val:
            return 'win'
        if player_val < dealer_val:
            return 'lose'
        return 'push'

    def _final_message(self, player, dealer, p_val, d_val, result):
        result_text = {
            'win': '你贏了！🎉',
            'lose': '你輸了。',
            'push': '平手。'
        }[result]
        return (
            f"結算：\n"
            f"你的手牌：{render_cards(player)}（{p_val}）\n"
            f"莊家手牌：{render_cards(dealer)}（{d_val}）\n"
            f"結果：{result_text}  使用 `!blackjack` 可再來一局。"
        )

    def _settle_and_format_single(self, table, player, dealer, p_val, d_val, result) -> str:
        owner_id = table['owner_id']
        bet = table.get('bet', 0)
        delta = 0
        if bet > 0:
            if result == 'win':
                delta = bet
            elif result == 'lose':
                delta = -bet
            self.user_points[owner_id] = self.user_points.get(owner_id, self.STARTING_POINTS) + delta
            self._save_points()
        result_text = {
            'win': '你贏了！🎉',
            'lose': '你輸了。',
            'push': '平手。'
        }[result]
        balance_txt = f"目前積分：{self.user_points.get(owner_id, self.STARTING_POINTS)}"
        bet_txt = f"（賭注：{bet} / 淨得：{delta:+}）" if bet > 0 else ''
        return (
            f"你的手牌：{render_cards(player)}（{p_val}）\n"
            f"莊家手牌：{render_cards(dealer)}（{d_val}）\n"
            f"結果：{result_text} {bet_txt}\n{balance_txt}\n使用 `!blackjack [賭注]` 可再來一局；用 `!point` 查看積分。"
        )

    def _status_message(self, table, reveal_dealer: bool) -> str:
        # 仍保留舊方法供黑傑克開局立即結束時使用
        player = table['hands'][0]
        dealer = table['dealer']
        p_val = hand_value(player)
        if reveal_dealer or table.get('finished'):
            d_part = f"莊家手牌：{render_cards(dealer)}（{hand_value(dealer)}）"
        else:
            d_visible = dealer[0]
            d_part = f"莊家明牌：{d_visible[0]}{d_visible[1]}  隱藏牌：🂠"
        return (
            f"你的手牌：{render_cards(player)}（{p_val}）\n" + d_part
        )

    def _status_message_multi(self, table, reveal_dealer: bool) -> str:
        parts = []
        for i, hand in enumerate(table['hands']):
            tag = '-> ' if i == table.get('active_index', 0) and not table['hand_done'][i] and not table.get('finished') else ''
            parts.append(f"{tag}手 {i+1}：{render_cards(hand)}（{hand_value(hand)}）")
        dealer = table['dealer']
        if reveal_dealer or table.get('finished'):
            d_part = f"莊家手牌：{render_cards(dealer)}（{hand_value(dealer)}）"
        else:
            d_visible = dealer[0]
            d_part = f"莊家明牌：{d_visible[0]}{d_visible[1]}  隱藏牌：🂠"
        return "\n".join(parts + [d_part])

    def _final_message_multi(self, table) -> str:
        # 先確保莊家點數
        d_val = hand_value(table['dealer'])
        owner_id = table['owner_id']
        bet = table.get('bet', 0)
        delta_total = 0
        results_lines = []
        for i, hand in enumerate(table['hands']):
            p_val = hand_value(hand)
            result = self._decide_result(p_val, d_val)
            result_text = {
                'win': '你贏了！🎉',
                'lose': '你輸了。',
                'push': '平手。'
            }[result]
            if bet > 0:
                if result == 'win':
                    delta_total += bet
                elif result == 'lose':
                    delta_total -= bet
            results_lines.append(f"手 {i+1}：{render_cards(hand)}（{p_val}）→ {result_text}")

        if bet > 0:
            self.user_points[owner_id] = self.user_points.get(owner_id, self.STARTING_POINTS) + delta_total
            self._save_points()

        balance_txt = f"目前積分：{self.user_points.get(owner_id, self.STARTING_POINTS)}"
        bet_txt = f"（賭注：{bet} / 淨得：{delta_total:+}）" if bet > 0 else ''
        summary = (
            "結算：\n" +
            "\n".join(results_lines) +
            "\n" +
            f"莊家手牌：{render_cards(table['dealer'])}（{d_val}）\n" +
            f"{bet_txt}\n{balance_txt}\n" +
            "使用 `!blackjack [賭注]` 可再來一局；用 `!point` 查看積分。"
        )
        return summary

    def _next_active_hand_index(self, table):
        for i, done in enumerate(table['hand_done']):
            if not done:
                return i
        return None

    # --- Embed builders ---
    def _build_status_embed(self, table, reveal_dealer: bool, footer_extra: str = None) -> discord.Embed:
        embed = discord.Embed(title='Blackjack', color=0x00bcd4)
        # 玩家手
        for i, hand in enumerate(table['hands']):
            tag = '➡️ ' if i == table.get('active_index', 0) and not table['hand_done'][i] and not table.get('finished') else ''
            embed.add_field(name=f"{tag}手 {i+1}", value=f"{render_cards(hand)}（{hand_value(hand)}）", inline=False)
        # 莊家
        if reveal_dealer or table.get('finished'):
            dealer_text = f"{render_cards(table['dealer'])}（{hand_value(table['dealer'])}）"
        else:
            d_visible = table['dealer'][0]
            dealer_text = f"{d_visible[0]}{d_visible[1]} 🂠"
        embed.add_field(name='莊家', value=dealer_text, inline=False)
        bet = table.get('bet', 0)
        if bet:
            embed.set_footer(text=f"賭注：{bet} {footer_extra or ''}")
        elif footer_extra:
            embed.set_footer(text=footer_extra)
        return embed

    def _build_final_embed(self, table) -> discord.Embed:
        # 確保已完成並有 dealer 值
        d_val = hand_value(table['dealer'])
        owner_id = table['owner_id']
        bet = table.get('bet', 0)
        delta_total = 0
        for i, hand in enumerate(table['hands']):
            p_val = hand_value(hand)
            result = self._decide_result(p_val, d_val)
            emoji = {'win': '✅', 'lose': '❌', 'push': '⚖️'}[result]
            text = {'win': '你贏了', 'lose': '你輸了', 'push': '平手'}[result]
            if bet > 0:
                if result == 'win':
                    delta_total += bet
                elif result == 'lose':
                    delta_total -= bet
        # 決定整體顏色：贏綠、輸紅、平手灰
        color = 0x43a047 if delta_total > 0 else (0xe53935 if delta_total < 0 else 0x9e9e9e)
        embed = discord.Embed(title='結算', color=color)
        # 再次加入每手內容（需在 embed 建立後）
        for i, hand in enumerate(table['hands']):
            p_val = hand_value(hand)
            result = self._decide_result(p_val, d_val)
            emoji = {'win': '✅', 'lose': '❌', 'push': '⚖️'}[result]
            text = {'win': '你贏了', 'lose': '你輸了', 'push': '平手'}[result]
            embed.add_field(name=f"手 {i+1}", value=f"{render_cards(hand)}（{p_val}） → {emoji} {text}", inline=False)
        embed.add_field(name='莊家', value=f"{render_cards(table['dealer'])}（{d_val}）", inline=False)
        if bet > 0:
            self.user_points[owner_id] = self.user_points.get(owner_id, self.STARTING_POINTS) + delta_total
            self._save_points()
            balance = self.user_points[owner_id]
            embed.set_footer(text=f"賭注：{bet} / 淨得：{delta_total:+}｜目前積分：{balance}")
        return embed

    @commands.command(name='point', help='查看你的目前積分')
    async def point(self, ctx):
        # 每次查詢前都重新讀取檔案，保證顯示最新值
        self.user_points = self._load_points()
        user_id = ctx.author.id
        if user_id not in self.user_points:
            self.user_points[user_id] = self.STARTING_POINTS
            self._save_points()
        await ctx.send(f"{ctx.author.mention} 目前積分：{self.user_points[user_id]}")

    # --- JSON 儲存/載入 ---
    def _load_points(self):
        try:
            if os.path.exists(self.points_path):
                with open(self.points_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # key 為字串的 user_id；轉回 int
                    return {int(k): int(v) for k, v in data.items()}
        except Exception:
            pass
        return {}

    def _save_points(self):
        try:
            os.makedirs(os.path.dirname(self.points_path), exist_ok=True)
            # 合併磁碟上的最新資料以避免覆蓋其他模組的更新
            on_disk = {}
            if os.path.exists(self.points_path):
                with open(self.points_path, 'r', encoding='utf-8') as rf:
                    try:
                        on_disk = json.load(rf)
                    except Exception:
                        on_disk = {}
            merged = {**on_disk, **{str(k): v for k, v in self.user_points.items()}}
            with open(self.points_path, 'w', encoding='utf-8') as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
        except Exception:
            # 靜默失敗，避免阻斷遊戲流程
            pass


async def setup(bot):
    await bot.add_cog(BlackjackCog(bot))


