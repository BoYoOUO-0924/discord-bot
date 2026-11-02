"""AI 連線層：使用 Ollama（可透過 Tailscale 連到別台電腦）。

提供的介面：text()、json()、check()。
- configured：有沒有設定（決定每日任務會不會出現海龜湯任務，所以一天內不能變動）
- available：設定好而且最近一次實測可以用（決定海龜湯能不能開局）
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

import aiohttp

log = logging.getLogger(__name__)


class AIError(Exception):
    """給使用者看的 AI 錯誤訊息。"""


class AIClient:
    provider = "none"

    def __init__(self, model: str = ""):
        self.model = model
        self.healthy: Optional[bool] = None   # None = 還沒檢查過
        self.status = "尚未檢查"
        self.checked_at: Optional[float] = None

    @property
    def configured(self) -> bool:
        return False

    @property
    def available(self) -> bool:
        return self.configured and self.healthy is not False

    def _mark(self, healthy: bool, status: str) -> None:
        if healthy != self.healthy:
            (log.info if healthy else log.warning)("AI（%s %s）狀態：%s", self.provider, self.model, status)
        self.healthy, self.status, self.checked_at = healthy, status, time.time()

    async def check(self) -> bool:
        """實際呼叫一次，確認 AI 能用。結果寫進 healthy / status。"""
        if not self.configured:
            self._mark(False, "未設定")
            return False
        try:
            await self.json("回傳 ok。", {"type": "object", "properties": {"ok": {"type": "boolean"}},
                                          "required": ["ok"]}, temperature=0, max_tokens=32)
        except AIError as e:
            self._mark(False, str(e))
            return False
        self._mark(True, "正常")
        return True

    async def text(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 256) -> str:
        raise AIError("AI 功能尚未設定。")

    async def json(self, prompt: str, schema: Dict, *, temperature: float = 0.2, max_tokens: int = 1024) -> Dict:
        raise AIError("AI 功能尚未設定。")

    @staticmethod
    def _parse_json(raw: str) -> Dict:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            data = json.loads(raw)
        except ValueError as e:
            log.warning("AI 回傳的 JSON 無法解析：%s", raw[:200])
            raise AIError("AI 回傳的格式有誤，請再試一次。") from e
        if not isinstance(data, dict):
            raise AIError("AI 回傳的格式有誤，請再試一次。")
        return data


class OllamaClient(AIClient):
    """呼叫 Ollama 的 /api/chat。用 format 指定 JSON schema，模型只能照格式回答。"""

    provider = "ollama"

    def __init__(self, url: Optional[str], model: str):
        super().__init__(model)
        self.url = (url or "").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.model)

    async def _chat(self, prompt: str, schema: Optional[Dict], temperature: float, max_tokens: int) -> str:
        if not self.configured:
            raise AIError("AI 功能尚未設定（缺少 OLLAMA_URL）。")
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,          # 判題不需要長考，關掉快很多
            "keep_alive": "30m",     # 讓模型留在記憶體，下一題不用重新載入
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if schema:
            body["format"] = schema
        # 遠端的 Ollama 偶爾會短暫不接受連線（例如正在載入模型），連線失敗時重試一次
        for attempt in (1, 2):
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90, connect=10)) as session:
                    async with session.post(f"{self.url}/api/chat", json=body) as resp:
                        data = await resp.json(content_type=None)
                        if resp.status != 200:
                            message = data.get("error") if isinstance(data, dict) else ""
                            self._mark(False, f"Ollama 回應錯誤（HTTP {resp.status}）{message}")
                            raise AIError("湯主暫時無法回應，請稍後再試。")
                break
            except AIError:
                raise
            except aiohttp.ClientConnectionError as e:   # 包含建立連線逾時
                if attempt == 1:
                    log.info("連不到 Ollama（%s），重試一次", type(e).__name__)
                    continue
                self._mark(False, f"連不到 Ollama（{self.url}）：{type(e).__name__}")
                raise AIError("湯主暫時離線，請稍後再試。") from e
            except asyncio.TimeoutError as e:   # 整體回應太久就不再重試，免得玩家等太久
                self._mark(False, "Ollama 回應逾時")
                raise AIError("湯主想太久了，請稍後再試。") from e
            except Exception as e:
                self._mark(False, f"Ollama 呼叫失敗：{type(e).__name__}")
                raise AIError("湯主暫時無法回應，請稍後再試。") from e
        text = ((data or {}).get("message") or {}).get("content", "").strip()
        if not text:
            raise AIError("AI 沒有產生內容，請再試一次。")
        return text

    async def text(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 256) -> str:
        return await self._chat(prompt, None, temperature, max_tokens)

    async def json(self, prompt: str, schema: Dict, *, temperature: float = 0.2, max_tokens: int = 1024) -> Dict:
        return self._parse_json(await self._chat(prompt, schema, temperature, max_tokens))

    async def check(self) -> bool:
        if not self.configured:
            self._mark(False, "未設定")
            return False
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(f"{self.url}/api/tags") as resp:
                    models = [m.get("name") for m in (await resp.json(content_type=None)).get("models", [])]
        except Exception as e:
            self._mark(False, f"連不到 Ollama（{self.url}）：{type(e).__name__}")
            return False
        if self.model not in models:
            self._mark(False, f"Ollama 上找不到模型 {self.model}")
            return False
        self._mark(True, "正常")
        return True


def create_ai(settings) -> AIClient:
    """建立 AI 湯主（Ollama）。沒設定 OLLAMA_URL 時 configured 為 False，海龜湯會停用。"""
    return OllamaClient(settings.ollama_url, settings.ollama_model)
