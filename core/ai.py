"""Gemini AI 包裝（google-genai 新版 SDK）。沒有設定金鑰時 available 為 False，其餘功能照常運作。"""
import json
import logging
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class AIError(Exception):
    """給使用者看的 AI 錯誤訊息。"""


class GeminiClient:
    def __init__(self, api_key: Optional[str], model: str):
        self.model = model
        self._client = None
        self._types = None
        if not api_key:
            return
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            log.warning("找不到 google-genai 套件，AI 功能停用")
            return
        self._client = genai.Client(api_key=api_key)
        self._types = types

    @property
    def available(self) -> bool:
        return self._client is not None

    def _config(self, *, temperature: float, fast: bool, json_schema: Optional[Dict] = None, max_tokens: int = 2048):
        types = self._types
        # 海龜湯常涉及死亡、犯罪等懸疑情節，預設的安全過濾容易誤擋
        safety = [
            types.SafetySetting(category=category, threshold=types.HarmBlockThreshold.BLOCK_NONE)
            for category in (
                types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            )
        ]
        kwargs: Dict[str, Any] = dict(temperature=temperature, max_output_tokens=max_tokens, safety_settings=safety)
        if fast:
            # 是非題裁判不需要長考，關掉思考可以快很多
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        if json_schema:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_json_schema"] = json_schema
        return types.GenerateContentConfig(**kwargs)

    async def _generate(self, prompt: str, config) -> str:
        if not self.available:
            raise AIError("AI 功能尚未設定（缺少 GEMINI_API_KEY）。")
        try:
            response = await self._client.aio.models.generate_content(model=self.model, contents=prompt, config=config)
        except Exception as e:  # SDK 會丟出各種網路 / API 錯誤
            code = getattr(e, "code", None)
            if code in (401, 403):
                log.error("Gemini 金鑰無效或專案沒有權限（%s），請到 Google AI Studio 重新產生金鑰：%s", code, e)
            elif code == 404:
                log.error("找不到 Gemini 模型 %s，請在 config.py 調整 GEMINI_MODEL：%s", self.model, e)
            else:
                log.exception("Gemini API 呼叫失敗")
            raise AIError("AI 暫時無法回應，請稍後再試。") from e
        text = (response.text or "").strip()
        if not text:
            raise AIError("AI 沒有產生內容（可能觸發了安全機制），請再試一次。")
        return text

    async def text(self, prompt: str, *, temperature: float = 0.2, fast: bool = True, max_tokens: int = 256) -> str:
        return await self._generate(prompt, self._config(temperature=temperature, fast=fast, max_tokens=max_tokens))

    async def json(self, prompt: str, schema: Dict, *, temperature: float = 0.9, fast: bool = False) -> Dict:
        raw = await self._generate(prompt, self._config(temperature=temperature, fast=fast, json_schema=schema, max_tokens=4096))
        # 保險起見，去掉模型偶爾仍會加上的 ``` 標記
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(raw)
        except ValueError as e:
            log.warning("AI 回傳的 JSON 無法解析：%s", raw[:200])
            raise AIError("AI 回傳的格式有誤，請再試一次。") from e
