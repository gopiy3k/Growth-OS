"""Contract test: the mock runtime satisfies the public AI Runtime contract.

This test needs NO Supabase, NO Hermes, NO paid services. It proves the contract
shape the EOS driver depends on. Any future runtime (OpenCode, …) must pass an
equivalent suite against its own endpoint.

Run:
    pytest tests/contract/test_runtime_contract.py
or:
    python -m pytest tests/contract/test_runtime_contract.py
(Requires `requests`; start the mock with `python tests/contract/mock_runtime.py`.)
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
import time

import pytest
import requests

# Launch the mock by file path (works without package __init__.py).
_MOCK_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "mock_runtime.py")


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def runtime_url():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, _MOCK_FILE, "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/v1"
    # wait for readiness
    for _ in range(50):
        try:
            requests.get(url, timeout=0.2)
            break
        except requests.RequestException:
            time.sleep(0.1)
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_runtime_returns_openai_compatible_shape(runtime_url):
    resp = requests.post(
        f"{runtime_url}/chat/completions",
        headers={"Authorization": "Bearer demo", "Content-Type": "application/json"},
        json={
            "model": "",
            "messages": [
                {"role": "system", "content": "return strict json"},
                {"role": "user", "content": "Title: x\nContent: y"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        },
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "model" in data, "runtime must publish its model (telemetry)"
    assert data["choices"], "must return at least one choice"
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)  # must be valid JSON
    assert isinstance(parsed.get("score"), int)
    assert parsed.get("category") in (
        "product", "industry", "competitor", "research", "opinion", "noise"
    )
    assert parsed.get("decision") in ("approve", "reject")


def test_runtime_requires_json_response_format(runtime_url):
    resp = requests.post(
        f"{runtime_url}/chat/completions",
        headers={"Authorization": "Bearer demo", "Content-Type": "application/json"},
        json={"model": "", "messages": [{"role": "user", "content": "hi"}]},
        timeout=10,
    )
    assert resp.status_code in (400, 422), "contract requires json response_format"
