"""OpenAI 兼容 API 客户端（stdlib urllib，零额外依赖）。
Ollama 本地模型把 base_url 指向 http://localhost:11434/v1 即可。
"""
import json
import urllib.request


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, model: str,
                 embed_model: str = "text-embedding-3-small", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.embed_model = embed_model
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def complete(self, system: str, user: str) -> str:
        data = self._post("/chat/completions", {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        })
        return data["choices"][0]["message"]["content"]

    def embed(self, text: str) -> list[float]:
        data = self._post("/embeddings", {"model": self.embed_model, "input": text})
        return data["data"][0]["embedding"]
