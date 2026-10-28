import io
import json
import urllib.request
import unittest
from unittest.mock import patch
from autopaperreview.stream_transport import urlopen_chat


class Response(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


class StreamTransportTests(unittest.TestCase):
    def test_native_review_stream_discards_thinking_and_keeps_usage(self):
        events = [{"choices":[{"delta":{"reasoning_content":"private synthetic thinking"}}]},
            {"choices":[{"delta":{"content":"Synthetic review"},"finish_reason":"stop"}]},
            {"choices":[],"usage":{"completion_tokens":12}}]
        wire = b"".join(b"data: "+json.dumps(e).encode()+b"\n\n" for e in events)+b"data: [DONE]\n\n"
        with patch.object(urllib.request, "urlopen", lambda *a, **k: Response(wire)):
            body=json.loads(urlopen_chat(urllib.request.Request("https://synthetic.invalid"), .1))
        self.assertEqual(body["choices"][0]["message"]["content"], "Synthetic review")
        self.assertEqual(body["usage"]["completion_tokens"], 12)


    def test_native_review_rejects_a_truncated_stream(self):
        with patch.object(urllib.request, "urlopen", lambda *a, **k: Response(b'data: {"choices":[]}\n\n')):
            with self.assertRaisesRegex(ConnectionError, "completion marker"):
                urlopen_chat(urllib.request.Request("https://synthetic.invalid"), .1)
