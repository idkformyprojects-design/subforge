import json, re, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
STATE = {"cfg": {}, "calls": []}
LOCK = threading.Lock()
def reply(h, code, obj):
    b = json.dumps(obj).encode(); h.send_response(code); h.send_header("Content-Type","application/json")
    h.send_header("Content-Length", str(len(b))); h.end_headers(); h.wfile.write(b)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        with LOCK: reply(self, 200, {"calls": STATE["calls"]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.path == "/control":
            with LOCK: STATE["cfg"] = body; STATE["calls"] = []
            return reply(self, 200, {"ok": True})
        m = re.match(r"/v1beta/models/([^:]+):generateContent", self.path)
        model = m.group(1)
        parts = body["contents"][0]["parts"]
        prompt = " ".join(p.get("text", "") for p in parts)
        kind = "review" if "senior subtitle editor" in prompt else "translate" if 'Translate the "t" field' in prompt else "test" if "Reply with the word OK" in prompt else "transcribe"
        assert self.headers.get("x-goog-api-key") == "TESTKEY"
        with LOCK:
            c = STATE["cfg"].get(model, {})
            n = sum(1 for x in STATE["calls"] if x[0] == model) + 1
            STATE["calls"].append([model, kind])
        if c.get("missing"): return reply(self, 404, {"error": {"message": f"models/{model} is not found"}})
        if n <= c.get("busy", 0): return reply(self, 503, {"error": {"message": "This model is currently experiencing high demand."}})
        if kind == "transcribe":
            out = [{"start": 1.0 + 10*i, "end": 3.0 + 10*i, "text": f"line {i} by {model}"} for i in range(2)]
        elif kind == "review":
            draft = json.loads(prompt[prompt.index("Draft subtitles:") + len("Draft subtitles:"):].strip())
            out = [dict(d) for d in draft]
            if out: out[0]["text"] = out[0]["text"] + " (fixed)"
        elif kind == "translate":
            items = json.loads(prompt[prompt.index("Input:") + 6:].strip())
            out = [{"i": it["i"], "t": f"T[{model}]:{it['t']}"} for it in items]
        else:
            return reply(self, 200, {"candidates": [{"content": {"parts": [{"text": "OK"}]}, "finishReason": "STOP"}]})
        reply(self, 200, {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(out) + "\n```"}]}, "finishReason": "STOP"}]})
ThreadingHTTPServer(("127.0.0.1", 18777), H).serve_forever()
