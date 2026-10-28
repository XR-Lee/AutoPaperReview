import io
import json
import urllib.request
import pytest
from autopaperreview.stream_transport import urlopen_chat


class Response(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


def test_native_review_stream_discards_thinking_and_keeps_usage(monkeypatch):
    events = [{"choices":[{"delta":{"reasoning_content":"private synthetic thinking"}}]},
        {"choices":[{"delta":{"content":"Synthetic review"},"finish_reason":"stop"}]},
        {"choices":[],"usage":{"completion_tokens":12}}]
    wire = b"".join(b"data: "+json.dumps(e).encode()+b"\n\n" for e in events)+b"data: [DONE]\n\n"
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response(wire))
    body=json.loads(urlopen_chat(urllib.request.Request("https://synthetic.invalid"), .1))
    assert body["choices"][0]["message"]["content"]=="Synthetic review"
    assert body["usage"]["completion_tokens"]==12


def test_native_review_rejects_a_truncated_stream(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Response(b'data: {"choices":[]}\n\n'))
    with pytest.raises(ConnectionError,match="completion marker"):
        urlopen_chat(urllib.request.Request("https://synthetic.invalid"), .1)
