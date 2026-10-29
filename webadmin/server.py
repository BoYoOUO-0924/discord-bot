"""管理後台的網頁伺服器（跟 bot 跑在同一個程式裡，共用資料庫連線與遊戲狀態）。

安全設計：
- 一定要設定 ADMIN_PASSWORD 才會啟動；密碼用 hmac.compare_digest 比對
- 同一個 IP 10 分鐘內登入失敗 5 次就暫時鎖住
- 登入後發 HttpOnly + SameSite=Strict 的 session cookie
- 所有修改類請求必須帶 X-Requested-With 標頭（跨站表單無法加自訂標頭，可防 CSRF）
- 所有管理操作都寫入 admin_audit 紀錄
"""
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, Optional

from aiohttp import web

from core.quests import daily_quests
from core.timeutil import today_tw

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SESSION_COOKIE = "gawa_admin"
SESSION_TTL = 7 * 86400
LOGIN_WINDOW, LOGIN_MAX_FAILS = 600, 5
CSRF_HEADER, CSRF_VALUE = "X-Requested-With", "gawa-admin"
PAGE_SIZE = 50
PLACEHOLDER_PASSWORDS = {"請改成你自己的密碼", "YOUR_ADMIN_PASSWORD"}

GAME_NAMES = {"blackjack": "21點", "slots": "拉霸機", "guess": "猜數字", "tictactoe": "井字遊戲",
              "poker": "德州撲克", "seatortoise": "海龜湯"}


def password_problem(password: Optional[str]) -> Optional[str]:
    """密碼不合格時回傳原因，合格回傳 None。"""
    if not password:
        return "沒有設定 ADMIN_PASSWORD"
    if password in PLACEHOLDER_PASSWORDS:
        return "ADMIN_PASSWORD 還是範本的預設值"
    if len(password) < 8:
        return "ADMIN_PASSWORD 至少要 8 個字元"
    return None


class AdminServer:
    def __init__(self, bot, password: str, host: str, port: int, log_path: str):
        self.bot, self.password, self.host, self.port, self.log_path = bot, password, host, port, log_path
        self.sessions: Dict[str, float] = {}
        self.failures: Dict[str, Deque[float]] = defaultdict(deque)
        self.started_at = time.time()
        self.version = "?"
        self.runner: Optional[web.AppRunner] = None
        self.app = self.build_app()

    @property
    def db(self):
        return self.bot.db

    # ================================================================== 啟動 / 關閉
    def build_app(self) -> web.Application:
        app = web.Application(middlewares=[self.security_headers, self.auth])
        app.router.add_get("/", self.index)
        app.router.add_static("/static/", STATIC_DIR)
        routes = [
            ("POST", "/api/login", self.login), ("POST", "/api/logout", self.logout),
            ("GET", "/api/session", self.session),
            ("GET", "/api/overview", self.overview),
            ("GET", "/api/users", self.users), ("GET", "/api/users/{user_id:\\d+}", self.user_detail),
            ("GET", "/api/users/{user_id:\\d+}/transactions", self.user_transactions),
            ("POST", "/api/users/{user_id:\\d+}/points", self.adjust_points),
            ("POST", "/api/users/{user_id:\\d+}/streak", self.set_streak),
            ("POST", "/api/users/{user_id:\\d+}/reset-work", self.reset_work),
            ("GET", "/api/games", self.games), ("POST", "/api/games/end", self.end_game),
            ("POST", "/api/holds/{hold_id:\\d+}/refund", self.refund_hold),
            ("GET", "/api/logs", self.logs), ("GET", "/api/audit", self.audit_log),
        ]
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
        return app

    async def start(self) -> None:
        try:
            from core.updater import git
            self.version = (await git(str(Path(__file__).resolve().parent.parent), "rev-parse", "--short", "HEAD"))
        except Exception:
            pass
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, self.host, self.port).start()

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    # ================================================================== 中介層
    @web.middleware
    async def security_headers(self, request: web.Request, handler):
        response = await handler(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' https://cdn.discordapp.com data:; "
            "style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @web.middleware
    async def auth(self, request: web.Request, handler):
        if request.path.startswith("/api/") and request.path != "/api/login":
            if not self.valid_session(request):
                return web.json_response({"error": "請先登入"}, status=401)
        if request.method != "GET" and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return web.json_response({"error": "請求格式不正確"}, status=403)
        return await handler(request)

    def valid_session(self, request: web.Request) -> bool:
        token = request.cookies.get(SESSION_COOKIE)
        expiry = self.sessions.get(token or "")
        if not expiry or expiry < time.time():
            self.sessions.pop(token or "", None)
            return False
        return True

    @staticmethod
    def client_ip(request: web.Request) -> str:
        return request.remote or "?"

    async def record(self, request: web.Request, action: str, user_id: Optional[int], detail: str) -> None:
        await self.db.audit(action, user_id, detail, self.client_ip(request))
        log.info("後台操作 %s（%s）：%s", action, self.client_ip(request), detail)

    # ================================================================== 頁面與登入
    async def index(self, request: web.Request):
        return web.FileResponse(STATIC_DIR / "index.html")

    async def login(self, request: web.Request):
        ip = self.client_ip(request)
        recent = self.failures[ip]
        now = time.time()
        while recent and now - recent[0] > LOGIN_WINDOW:
            recent.popleft()
        if len(recent) >= LOGIN_MAX_FAILS:
            wait = int(LOGIN_WINDOW - (now - recent[0])) + 1
            return web.json_response({"error": f"登入失敗太多次，請 {wait // 60 + 1} 分鐘後再試"}, status=429)
        body = await self.json_body(request)
        if not hmac.compare_digest(str(body.get("password", "")).encode(), self.password.encode()):
            recent.append(now)
            log.warning("後台登入失敗（%s）", ip)
            return web.json_response({"error": "密碼錯誤"}, status=401)
        recent.clear()
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + SESSION_TTL
        await self.record(request, "login", None, "登入後台")
        response = web.json_response({"ok": True})
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="Strict", path="/")
        return response

    async def logout(self, request: web.Request):
        self.sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    async def session(self, request: web.Request):
        return web.json_response({"ok": True})

    @staticmethod
    async def json_body(request: web.Request) -> Dict:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    # ================================================================== 總覽與統計
    async def overview(self, request: web.Request):
        today = today_tw()
        series = await self.db.stats_daily_series(today, 14)
        days = [{"day": day,
                 "checkin": v.get("checkin", 0), "work": v.get("work", 0), "quest": v.get("quest", 0),
                 "other": v.get("game_reward", 0) + v.get("chat", 0), "active": v.get("active", 0)}
                for day, v in series.items()]
        games = [{"game": r["game"], "name": GAME_NAMES.get(r["game"], r["game"]), "played": r["played"],
                  "won": r["won"], "net": r["net"], "players": r["players"]} for r in await self.db.stats_games()]
        bot = self.bot
        return web.json_response({
            "kpi": await self.db.stats_overview(today),
            "days": days,
            "games": games,
            "bot": {
                "name": str(bot.user) if bot.user else "未登入",
                "online": bot.is_ready() and not bot.is_closed(),
                "latency_ms": round(bot.latency * 1000) if bot.latency == bot.latency else None,
                "guilds": len(bot.guilds),
                "uptime": int(time.time() - self.started_at),
                "version": self.version,
                "ai": bot.ai.available,
                "ai_configured": bot.ai.configured,
                "ai_name": f"{bot.ai.provider} {bot.ai.model}".strip(),
                "ai_status": bot.ai.status,
                "auto_update": bot.settings.auto_update,
            },
        })

    # ================================================================== 使用者
    async def display_name(self, user_id: int, name: Optional[str], avatar: Optional[str]):
        if name:
            return name, avatar
        user = self.bot.get_user(user_id)
        if user:
            await self.db.remember_user(user_id, user.display_name, user.display_avatar.url)
            return user.display_name, user.display_avatar.url
        return None, None

    async def users(self, request: web.Request):
        page = max(1, int(request.query.get("page", 1) or 1))
        total, rows = await self.db.search_users(request.query.get("q", ""), request.query.get("sort", "points"),
                                                 PAGE_SIZE, (page - 1) * PAGE_SIZE)
        today = today_tw().isoformat()
        items = []
        for r in rows:
            name, avatar = await self.display_name(r["user_id"], r["name"], r["avatar_url"])
            items.append({"id": str(r["user_id"]), "name": name, "avatar": avatar, "points": r["points"],
                          "streak": r["streak"], "last_checkin": r["last_checkin"], "checked_today": r["last_checkin"] == today,
                          "checkins": r["total_checkins"], "games": r["games"]})
        return web.json_response({"total": total, "page": page, "page_size": PAGE_SIZE, "items": items})

    async def user_detail(self, request: web.Request):
        user_id = int(request.match_info["user_id"])
        row = await self.db.user_row(user_id)
        if not row:
            return web.json_response({"error": "找不到這位使用者"}, status=404)
        today = today_tw()
        profile = await self.db.get_profile(user_id, today)
        name, avatar = await self.display_name(user_id, row["name"], row["avatar_url"])
        last_work, chat_today = await self.db.income_status(user_id, today)
        progress = await self.db.quest_progress(user_id, today)
        quests = [{"title": q.title, "target": q.target, "reward": q.reward,
                   "progress": progress.get(q.key, (0, False))[0], "done": progress.get(q.key, (0, False))[1]}
                  for q in daily_quests(user_id, today, self.bot.ai.configured)]
        holds = [{"id": h["id"], "amount": h["amount"], "reason": h["reason"], "created_at": h["created_at"]}
                 for h in await self.db.list_holds() if h["user_id"] == user_id]
        stats = [{"game": s["game"], "name": GAME_NAMES.get(s["game"], s["game"]), "played": s["played"],
                  "won": s["won"], "net": s["net"]} for s in await self.db.game_stats(user_id)]
        reminders = [{"content": r["content"], "due_at": r["due_at"]} for r in await self.db.list_reminders(user_id)]
        return web.json_response({
            "id": str(user_id), "name": name, "avatar": avatar, "points": row["points"],
            "streak": profile.streak, "last_checkin": row["last_checkin"], "checkins": row["total_checkins"],
            "last_work": last_work, "chat_today": chat_today, "quests": quests, "holds": holds,
            "games": stats, "reminders": reminders,
        })

    async def user_transactions(self, request: web.Request):
        user_id = int(request.match_info["user_id"])
        page = max(1, int(request.query.get("page", 1) or 1))
        total, rows = await self.db.transactions(user_id, PAGE_SIZE, (page - 1) * PAGE_SIZE)
        return web.json_response({"total": total, "page": page, "page_size": PAGE_SIZE,
                                  "items": [{"amount": r["amount"], "reason": r["reason"], "at": r["created_at"]}
                                            for r in rows]})

    async def adjust_points(self, request: web.Request):
        user_id = int(request.match_info["user_id"])
        body = await self.json_body(request)
        reason = str(body.get("reason", "")).strip()[:100]
        mode = body.get("mode")
        try:
            amount = int(body.get("amount"))
        except (TypeError, ValueError):
            return web.json_response({"error": "金額必須是整數"}, status=400)
        if not reason:
            return web.json_response({"error": "請填寫調整原因"}, status=400)
        if mode == "set":
            if not 0 <= amount <= 10**12:
                return web.json_response({"error": "積分必須介於 0 到 1 兆"}, status=400)
            old, new = await self.db.admin_adjust(user_id, reason, set_to=amount)
        elif mode == "add":
            if amount == 0 or abs(amount) > 10**12:
                return web.json_response({"error": "調整金額不能是 0，也不能超過 1 兆"}, status=400)
            old, new = await self.db.admin_adjust(user_id, reason, delta=amount)
        else:
            return web.json_response({"error": "未知的調整方式"}, status=400)
        await self.record(request, "points", user_id, f"積分 {old:,} → {new:,}（{reason}）")
        return web.json_response({"ok": True, "old": old, "new": new})

    async def set_streak(self, request: web.Request):
        user_id = int(request.match_info["user_id"])
        body = await self.json_body(request)
        try:
            streak = int(body.get("streak"))
        except (TypeError, ValueError):
            return web.json_response({"error": "天數必須是整數"}, status=400)
        if not 0 <= streak <= 10000:
            return web.json_response({"error": "天數必須介於 0 到 10000"}, status=400)
        await self.db.admin_set_streak(user_id, streak, today_tw())
        await self.record(request, "streak", user_id, f"連續簽到改為 {streak} 天")
        return web.json_response({"ok": True})

    async def reset_work(self, request: web.Request):
        user_id = int(request.match_info["user_id"])
        await self.db.admin_reset_work(user_id)
        await self.record(request, "reset_work", user_id, "重設打工冷卻")
        return web.json_response({"ok": True})

    # ================================================================== 遊戲與保管中的賭注
    def channel_name(self, channel_id: int) -> str:
        channel = self.bot.get_channel(channel_id)
        return f"#{channel.name}" if channel and hasattr(channel, "name") else str(channel_id)

    def attached_holds(self) -> Dict[int, str]:
        """正在進行的遊戲占用的保管 ID → 遊戲名稱。沒被占用的保管才可以手動退還。"""
        attached: Dict[int, str] = {}
        blackjack, poker = self.bot.get_cog("Blackjack"), self.bot.get_cog("Poker")
        for game in getattr(blackjack, "games", {}).values():
            if game.hold_id and not game.finished:
                attached[game.hold_id] = "21點"
        for game in getattr(poker, "games", {}).values():
            for hold_id in game.holds.values():
                attached[hold_id] = "德州撲克"
        for lobby in getattr(poker, "lobbies", {}).values():
            for hold_id in lobby.holds.values():
                attached[hold_id] = "德州撲克大廳"
        return attached

    async def games(self, request: web.Request):
        items = []
        poker = self.bot.get_cog("Poker")
        for channel_id, game in getattr(poker, "games", {}).items():
            t = game.table
            items.append({"type": "poker", "id": str(channel_id), "game": "德州撲克", "where": self.channel_name(channel_id),
                          "detail": f"第 {t.hand_number} 手・底池 {t.pot:,}・盲注 {t.small_blind}/{t.big_blind}",
                          "players": [f"{s.name}（{s.stack:,}）" for s in t.seats]})
        for channel_id, lobby in getattr(poker, "lobbies", {}).items():
            items.append({"type": "poker_lobby", "id": str(channel_id), "game": "德州撲克大廳",
                          "where": self.channel_name(channel_id),
                          "detail": f"等待開局・帶入 {lobby.buy_in:,}",
                          "players": [m.display_name for m in lobby.players.values()] + [p.name for p in lobby.ai.values()]})
        blackjack = self.bot.get_cog("Blackjack")
        for user_id, game in getattr(blackjack, "games", {}).items():
            if game.finished:
                continue
            name, _ = await self.display_name(user_id, None, None)
            items.append({"type": "blackjack", "id": str(user_id), "game": "21點",
                          "where": self.channel_name(game.channel_id) if game.channel_id else "?",
                          "detail": f"賭注 {game.bet:,}", "players": [name or str(user_id)]})
        guess = self.bot.get_cog("GuessNumber")
        for channel_id, game in getattr(guess, "games", {}).items():
            items.append({"type": "guess", "id": str(channel_id), "game": "猜數字", "where": self.channel_name(channel_id),
                          "detail": f"範圍 {game.low}~{game.high}・已猜 {game.attempts} 次（答案 {game.answer}）",
                          "players": [f"{len(game.players)} 人參與"]})
        soup = self.bot.get_cog("SeaTortoise")
        for channel_id, game in getattr(soup, "games", {}).items():
            items.append({"type": "seatortoise", "id": str(channel_id), "game": "海龜湯",
                          "where": self.channel_name(channel_id),
                          "detail": f"「{game.puzzle.title}」・已提問 {len(game.qa)} 題",
                          "players": [f"{len(game.participants)} 人參與"]})

        attached = self.attached_holds()
        holds = [{"id": h["id"], "user_id": str(h["user_id"]), "name": h["name"], "amount": h["amount"],
                  "reason": h["reason"], "created_at": h["created_at"], "in_use": attached.get(h["id"])}
                 for h in await self.db.list_holds()]
        return web.json_response({"games": items, "holds": holds})

    async def end_game(self, request: web.Request):
        body = await self.json_body(request)
        kind = body.get("type")
        try:
            key = int(body.get("id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "缺少遊戲 ID"}, status=400)
        reason = "管理員從後台結束了這局"
        if kind == "poker":
            cog = self.bot.get_cog("Poker")
            game = cog.games.get(key) if cog else None
            if not game:
                return web.json_response({"error": "這局已經結束了"}, status=404)
            async with game.lock:
                await cog.end_game(game, f"{reason}，剩餘籌碼已退回積分。")
        elif kind == "poker_lobby":
            cog = self.bot.get_cog("Poker")
            lobby = cog.lobbies.get(key) if cog else None
            if not lobby:
                return web.json_response({"error": "這個大廳已經關閉了"}, status=404)
            await cog.close_lobby(lobby, f"{reason}，帶入籌碼已全數退回。")
        elif kind == "blackjack":
            cog = self.bot.get_cog("Blackjack")
            game = cog.games.get(key) if cog else None
            if not game or game.finished:
                return web.json_response({"error": "這局已經結束了"}, status=404)
            async with game.lock:
                if not game.finished:
                    game.stand()   # 依目前手牌直接停牌結算
                    await cog.finish(game)
        elif kind == "guess":
            cog = self.bot.get_cog("GuessNumber")
            game = cog.games.get(key) if cog else None
            if not game:
                return web.json_response({"error": "這局已經結束了"}, status=404)
            await cog.end(game, f"🛑 {reason}")
        elif kind == "seatortoise":
            cog = self.bot.get_cog("SeaTortoise")
            game = cog.games.get(key) if cog else None
            if not game:
                return web.json_response({"error": "這局已經結束了"}, status=404)
            await cog.reveal(game, f"{reason}並公布答案")
        else:
            return web.json_response({"error": "未知的遊戲類型"}, status=400)
        await self.record(request, "end_game", None, f"結束 {GAME_NAMES.get(kind, kind)}（{key}）")
        return web.json_response({"ok": True})

    async def refund_hold(self, request: web.Request):
        hold_id = int(request.match_info["hold_id"])
        if hold_id in self.attached_holds():
            return web.json_response({"error": "這筆賭注還在進行中的遊戲裡，請先結束那局"}, status=409)
        hold = await self.db.hold_info(hold_id)
        if not hold:
            return web.json_response({"error": "找不到這筆保管中的賭注"}, status=404)
        await self.db.settle(hold_id, hold["amount"], f"管理員退還保管中的賭注（{hold['reason']}）")
        await self.record(request, "refund_hold", hold["user_id"], f"退還保管中的賭注 {hold['amount']:,}（{hold['reason']}）")
        return web.json_response({"ok": True})

    # ================================================================== 紀錄
    async def logs(self, request: web.Request):
        lines = min(2000, max(10, int(request.query.get("lines", 300) or 300)))
        level = request.query.get("level", "")
        if not os.path.exists(self.log_path):
            return web.json_response({"lines": []})
        with open(self.log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 512 * 1024))   # 只讀最後 512 KB
            text = f.read().decode("utf-8", errors="replace")
        rows = text.splitlines()
        if level:
            rows = [r for r in rows if f"[{level}" in r or (level == "ERROR" and r.startswith(("Traceback", "  ")))]
        return web.json_response({"lines": rows[-lines:]})

    async def audit_log(self, request: web.Request):
        rows = await self.db.list_audit(200)
        return web.json_response({"items": [
            {"at": r["created_at"], "action": r["action"], "user_id": str(r["user_id"]) if r["user_id"] else None,
             "name": r["name"], "detail": r["detail"], "ip": r["ip"]} for r in rows]})
