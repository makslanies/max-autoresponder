"""LLM-клиент: Cloudflare Workers AI (REST API).

Модель и параметры — в config.yaml (секция llm).
Ключи берутся из .env: CLOUDFLARE_ACCOUNT_ID и CF_AIG_TOKEN.
"""

import logging
import os

import httpx


class LLMClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        account_id = os.environ.get(cfg.get("account_id_env", "CLOUDFLARE_ACCOUNT_ID"))
        token = os.environ.get(cfg.get("api_token_env", "CF_AIG_TOKEN"))
        if not account_id or not token:
            missing = cfg.get("account_id_env", "CLOUDFLARE_ACCOUNT_ID") if not account_id \
                else cfg.get("api_token_env", "CF_AIG_TOKEN")
            raise SystemExit(f"Не задана переменная окружения {missing} (см. .env)")

        self.url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{cfg['model']}"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self.temperature = cfg.get("temperature", 0.7)
        self.max_history = cfg.get("max_history", 8)
        self.system_prompt = cfg.get("system_prompt", "Ты — ассистент в мессенджере.")

    async def reply(self, history: list[dict]) -> str:
        """history: [{'role': 'user'|'assistant', 'content': str}, ...] — последняя переписка."""
        messages = [{"role": "system", "content": self.system_prompt}]
        messages += [
            {"role": m["role"], "content": m["content"][:4000]}
            for m in history[-self.max_history:]
        ]

        payload = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": 500,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(self.url, headers=self.headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = (data["result"]["choices"][0]["message"]["content"] or "").strip()
        logging.info("Workers AI ответ (%d симв.): %s", len(text), text[:120])
        return text
