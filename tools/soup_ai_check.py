"""海龜湯湯主實測：用真的 Ollama 跑一批問題，檢查回答對不對、前後一不一致。

用法（在能連到 Ollama 的電腦上，於專案資料夾執行）：
    python -m tools.soup_ai_check
    python -m tools.soup_ai_check --url http://127.0.0.1:11434 --model gemma4:e4b-it-q4_K_M

沒有指定 --url / --model 時讀 .env 的 OLLAMA_URL / OLLAMA_MODEL。
不會連 Discord，也不會動到資料庫。

檢查三件事：
1. 正確率：每題有預期答案（有些題目「是」或「是也不是」都算對）
2. 換句話說：意思一樣的問題，回答應該一樣
3. 重現性：同一題在新的一局再問一次，回答應該一樣
"""
import argparse
import asyncio
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.seatortoise import RAW_ANSWER, SeaTortoise, SoupGame, load_bank  # noqa: E402
from core.ai import AIError, OllamaClient  # noqa: E402

# (題目編號, [(換句話說的群組, 問題, 可接受的答案), ...])
# 同一個群組的問題意思相同，回答必須一樣
CASES = {
    "h05": [
        ("懷孕", "她懷孕了嗎？", {"是"}),
        ("懷孕", "小琳肚子裡有寶寶嗎？", {"是"}),
        ("懷孕", "小琳有小孩了嗎？", {"是", "是也不是"}),
        ("生病", "她生病了嗎？", {"不是"}),
        ("生病", "小琳身體有疾病嗎？", {"不是"}),
        ("高興", "她被拒絕是好事嗎？", {"是"}),
        ("高興", "被拒絕讓她很開心嗎？", {"是"}),
        ("護理師", "她認識那個護理師嗎？", {"不是", "無關"}),
        ("老公", "她有結婚嗎？", {"是"}),
        ("老公", "小琳有老公嗎？", {"是"}),
        ("想要", "她一直想要小孩嗎？", {"是"}),
        ("捐血", "她以後不能再捐血了嗎？", {"不是", "是也不是"}),
        ("性別", "小琳是男生嗎？", {"不是"}),
        ("天氣", "那天有下雨嗎？", {"無關"}),
        ("錢", "跟錢有關嗎？", {"不是", "無關"}),
    ],
    "h22": [
        ("謀殺", "屋主是被殺的嗎？", {"是"}),
        ("謀殺", "這是他殺嗎？", {"是"}),
        ("意外", "屋主是自己跌倒摔死的嗎？", {"不是"}),
        ("意外", "屋主是意外死亡的嗎？", {"不是"}),
        ("深", "腳印比正常的深嗎？", {"是"}),
        ("深", "腳印踩得特別深嗎？", {"是"}),
        ("揹", "兇手揹著屍體走過去嗎？", {"是"}),
        ("揹", "屍體是被人背到礁石的嗎？", {"是"}),
        ("鞋", "腳印是死者的鞋子踩的嗎？", {"是"}),
        ("鞋", "兇手穿了死者的鞋嗎？", {"是"}),
        ("自己走", "屋主是自己走到礁石的嗎？", {"不是"}),
        ("回程", "兇手的回程腳印被海浪沖掉了嗎？", {"是"}),
        ("人數", "兇手有兩個人以上嗎？", {"不是", "無關"}),
        ("天氣", "那天是晴天嗎？", {"無關"}),
    ],
}


async def run(url: str, model: str, ids) -> int:
    ai = OllamaClient(url, model)
    print(f"Ollama：{url}　模型：{model}")
    if not await ai.check():
        print(f"❌ 連不上或找不到模型：{ai.status}")
        return 2
    cog = SeaTortoise.__new__(SeaTortoise)
    cog.bot = SimpleNamespace(ai=ai)
    bank = {p.id: p for p in load_bank()}

    total = correct = 0
    group_conflicts, unstable, times = [], [], []
    for pid in ids:
        puzzle = bank[pid]
        print(f"\n🐢 {pid} {puzzle.title}")
        game = SoupGame(0, 0, puzzle)   # 照順序問，前面的問答會當成上下文
        groups = {}
        for group, question, ok in CASES[pid]:
            start = time.perf_counter()
            try:
                display = await cog.judge_question(game, question)
            except AIError as e:
                print(f"  ⚠️ {question} → 錯誤：{e}")
                continue
            times.append(time.perf_counter() - start)
            game.qa.append(("測試", question, display))
            answer = RAW_ANSWER[display]
            total += 1
            good = answer in ok
            correct += good
            print(f"  {'✅' if good else '❌'} {question} → {answer}" + ("" if good else f"（預期：{'/'.join(sorted(ok))}）"))
            groups.setdefault(group, []).append((question, answer))

            # 重現性：開新的一局（沒有前面的問答）再問一次
            fresh = await cog.judge_question(SoupGame(0, 0, puzzle), question)
            if RAW_ANSWER[fresh] != answer:
                unstable.append(f"{pid} {question}：有上下文 {answer}／新局 {RAW_ANSWER[fresh]}")
        for group, answers in groups.items():
            if len({a for _, a in answers}) > 1:
                group_conflicts.append(f"{pid}「{group}」：" + "、".join(f"{q}→{a}" for q, a in answers))

    print("\n========== 結果 ==========")
    print(f"正確率：{correct}/{total}（{correct / max(total, 1):.0%}）")
    print(f"平均回應時間：{sum(times) / max(len(times), 1):.1f} 秒")
    print(f"換句話說卻答案不同：{len(group_conflicts)} 組")
    for line in group_conflicts:
        print(f"  - {line}")
    print(f"有沒有上下文答案不同：{len(unstable)} 題（不一定是錯，只是參考）")
    for line in unstable:
        print(f"  - {line}")
    return 0 if correct == total and not group_conflicts else 1


def main():
    parser = argparse.ArgumentParser(description="海龜湯湯主實測")
    parser.add_argument("--url")
    parser.add_argument("--model")
    parser.add_argument("--puzzle", action="append", choices=list(CASES), help="只測某一題，可重複指定")
    args = parser.parse_args()
    url, model = args.url, args.model
    if not url or not model:
        from core.config import load_settings
        settings = load_settings()
        url, model = url or settings.ollama_url, model or settings.ollama_model
    if not url:
        sys.exit("沒有 Ollama 網址：請用 --url 指定，或在 .env 設定 OLLAMA_URL")
    sys.exit(asyncio.run(run(url, model, args.puzzle or list(CASES))))


if __name__ == "__main__":
    main()
