"""Test-only OpenAI-compatible server. Never imported by product code.

Run: python tests/fake_upstream.py [port]
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 64


def embed(text: str) -> list[float]:
    vector = [0.0] * DIM
    for char in text:
        index = hashlib.md5(char.encode("utf-8")).digest()[0] % DIM
        vector[index] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 8) for value in vector]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send({"error": {"message": "bad json"}}, 400)
            return
        if self.path.endswith("/rerank") or self.path.endswith("/text-rerank"):
            documents = body.get("documents")
            if documents is None:
                documents = (body.get("input") or {}).get("documents") or []
            results = sorted(
                ({"index": index, "relevance_score": float(index + 1)}
                 for index in range(len(documents))),
                key=lambda item: item["relevance_score"],
                reverse=True,
            )
            payload = {"output": {"results": results}} if self.path.endswith("/text-rerank") else {"results": results}
            self._send(payload)
            return
        if self.path.endswith("/embeddings"):
            inputs = body.get("input", [])
            if isinstance(inputs, str):
                inputs = [inputs]
            data = [{"index": index, "embedding": embed(str(text)), "object": "embedding"}
                    for index, text in enumerate(inputs)]
            self._send({"object": "list", "data": data, "model": body.get("model", "fake")})
            return
        if self.path.endswith("/chat/completions"):
            messages = body.get("messages", [])
            question = ""
            for message in reversed(messages):
                if message.get("role") == "user":
                    question = str(message.get("content", ""))
                    break
            if "标准答案" in question:
                content = '{"score": 4, "reason": "回答覆盖主要要点"}'
            elif "年会" in question or "合同金额" in question:
                content = "知识库中没有找到相关依据，无法回答。"
            else:
                content = "根据资料，上传文档支持 PDF、Word、Markdown 三种格式[1]。"
            self._send({"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                        "model": body.get("model", "fake")})
            return
        self._send({"error": {"message": "not found"}}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()