"""Chat SSE envelope assembly; timeout measures socket silence, not total time."""
import json
import urllib.request


def urlopen_chat(request, timeout):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if "text/event-stream" not in getattr(response, "headers", {}).get("Content-Type", ""):
            return response.read()
        text, usage, event, finished = [], {}, [], False
        size = 0
        def consume():
            nonlocal finished, usage
            data = b"\n".join(event).decode().strip()
            event.clear()
            if not data: return
            if data == "[DONE]": finished = True; return
            item = json.loads(data)
            if item.get("error"): raise RuntimeError(str(item["error"]))
            if item.get("usage"): usage = item["usage"]
            for choice in item.get("choices", []):
                delta = choice.get("delta", {})
                if isinstance(delta.get("content"), str): text.append(delta["content"])
                if delta.get("refusal"): raise ValueError("Model refused the review request")
                if choice.get("finish_reason") is not None: finished = True
        # readline consumes live bytes; socket timeout is renewed by incoming
        # bytes even while a long SSE event is being assembled.
        for line in response:
            size += len(line)
            if size > 32_000_000: raise ValueError("Model response exceeded 32 MB")
            line = line.rstrip(b"\r\n")
            if not line: consume()
            elif line.startswith(b"data:"): event.append(line[5:].lstrip())
        consume()
        if not finished: raise ConnectionError("Model stream ended without a completion marker")
        return json.dumps({"choices": [{"message": {"role": "assistant", "content": "".join(text)}}], "usage": usage}).encode()
