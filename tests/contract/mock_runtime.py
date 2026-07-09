"""Growth OS mock AI Runtime — implements the public contract with stdlib only.

Purpose: let contributors run the FULL EOS spine (enqueue -> claim -> reason ->
persist) with ZERO dependency on Hermes, the private product, or any paid service.

It speaks the OpenAI-compatible POST /v1/chat/completions contract documented in
docs/runtime-contract.md and returns a deterministic, valid score result so the
reference driver's _normalize() produces a well-formed run record.

Run:
    python tests/contract/mock_runtime.py --port 8643
Then point the driver at it:
    export AI_RUNTIME_BASE_URL=http://127.0.0.1:8643/v1
    export AI_RUNTIME_API_KEY=demo
    python -m engines.content.lib.run_score_stage
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The capability this mock serves (matches the Content engine 'score' stage).
_MODEL = "mock-runtime-score_content-v1"

# Minimal but realistic score output. The driver normalizes this into the
# frozen contract (0..10, category from a fixed set, approve/reject).
_DEMO = {
    "score": 7,
    "category": "industry",
    "decision": "approve",
    "rationale": "Mock runtime: relevant industry signal, meets threshold.",
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return

        # Contract check: must request json_object / json_schema response format.
        rf = req.get("response_format", {}).get("type", "")
        if rf not in ("json_object", "json_schema"):
            self._send(422, {"error": "response_format must be json_object or json_schema"})
            return

        # Echo a snippet of the user message back so callers can see round-trip,
        # but keep the structured result valid per the frozen contract.
        content = json.dumps(_DEMO)
        self._send(200, {
            "model": _MODEL,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content},
                 "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })


def main() -> None:
    ap = argparse.ArgumentParser(description="Growth OS mock AI Runtime")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8643)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"mock-runtime listening on http://{args.host}:{args.port}/v1  (model={_MODEL})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
