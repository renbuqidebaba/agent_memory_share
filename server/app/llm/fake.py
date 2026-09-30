"""内置规则 provider：不依赖任何外部 API，用于测试和离线演示。

- complete(): 基于规则的确定性"提炼"（取 user 发言中的长句作为事实，截取拼接作为摘要）
- embed(): 字符 n-gram 哈希向量，语义上无意义但字符重合度高的文本向量相似，
  足以验证向量检索链路是否正确
"""
import hashlib
import json
import re

EMBED_DIM = 128


class FakeProvider:
    name = "fake"

    def complete(self, system: str, user: str) -> str:
        if "记忆提炼器" in system:
            return self._extract_facts(user)
        if "会话摘要器" in system:
            return self._summarize(user)
        return ""

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * EMBED_DIM
        # 字符 bigram 哈希：中文字符重合越多向量越近
        chars = [c for c in text if not c.isspace()]
        for i in range(len(chars)):
            for n in (1, 2):
                gram = "".join(chars[max(0, i - n + 1): i + 1])
                h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
                vec[h % EMBED_DIM] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    # ---------- 规则提炼 ----------
    def _extract_facts(self, transcript: str) -> str:
        facts = []
        for line in transcript.splitlines():
            m = re.match(r"\[(user|assistant)\]\s*(.+)", line.strip())
            if not m:
                continue
            text = m.group(2).strip()
            if len(text) >= 8:  # 过滤短闲聊
                facts.append({"content": text, "tags": ["auto-fact"]})
            if len(facts) >= 5:
                break
        return json.dumps(facts, ensure_ascii=False)

    def _summarize(self, user: str) -> str:
        lines = [ln.strip() for ln in user.splitlines() if ln.strip()]
        # 去掉"已有摘要：（无）"等前缀行，拼接对话要点
        points = [re.sub(r"^\[(user|assistant)\]\s*", "", ln) for ln in lines]
        points = [p for p in points if p and p not in ("（无）",) and not p.startswith("已有摘要")]
        summary = "；".join(points[-6:])
        return (summary[:180] + "…") if len(summary) > 180 else (summary or "（空会话）")
