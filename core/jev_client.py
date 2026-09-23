"""The only network egress in this program.

One JSON POST goes out per judgment; nothing is written to disk or logs. Ported
from 先回 (Android) `jev/PriorityClient.kt` + `jev/HttpJson.kt`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .msg_item import MsgItem
from . import questions as Q


class ApiError(Exception):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status} {body[:200]}")


def _post(url: str, api_key: str, payload: dict, timeout: int = 30) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
            # OpenRouter attribution; other hosts ignore these.
            "HTTP-Referer": "https://jev-priority.local",
            "X-Title": "Jev Priority",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ApiError(e.code, body) from e
    return json.loads(text)


class JevClient:
    """Scores one message on all four questions. Nothing generative here."""

    def __init__(self, api_key: str, model: str, endpoint: str) -> None:
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint

    def judge(self, item: MsgItem) -> None:
        """Throws on transport failure; the caller records it on the item."""
        payload = {
            "model": self.model,
            "state": Q.state(item),
            "questions": Q.questions(),
        }
        resp = _post(self.endpoint, self.api_key, payload)

        answers = resp.get("answers")
        if not isinstance(answers, dict):
            raise ValueError("响应里没有 answers 字段")

        level = answers.get("urgency_level") or {}
        raw = float(level.get("score") or 0.0)
        item.urgency = max(1, min(9, round(raw)))
        item.confidence = float(level.get("confidence") or 0.0)

        # Jev returns noul as the probability of "true".
        noul = float((answers.get("needs_reply_now") or {}).get("noul") or 0.0)
        item.needs_now = noul >= 0.5

        item.category = str((answers.get("category") or {}).get("choice") or "")
        item.reason = str((answers.get("why_urgent") or {}).get("choice") or "")
        item.judged = True
        item.error = None

    def probe(self) -> str:
        """Connectivity probe used by the settings screen."""
        probe_item = MsgItem(
            sender="同事",
            text="方案今天下班前能发我吗？老板在等",
            app_name="微信",
        )
        self.judge(probe_item)
        now = "要马上回" if probe_item.needs_now else "可以晚点"
        pct = round(probe_item.confidence * 100)
        return (
            f"紧急度 {probe_item.urgency}/9 · {now} · "
            f"类型 {probe_item.category} · 把握 {pct}%"
        )
