"""Tiny fake Ollama for UI work when no real server is around.

    python tools/mock_ollama.py            # listens on http://127.0.0.1:11435

Point the app at it in Settings -> AI Coach -> Server URL.
Streams /api/chat as NDJSON in deliberately awkward chunk sizes so the
client's line buffering gets exercised, and answers format=json requests
with a canned nutrition estimate.
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = ("**Key insight:** this is the mock server, so the numbers are made up.\n\n"
         "1. Protein is the gap - add 40g at lunch\n"
         "2. Keep Zone 2 to `30 min`\n"
         "- Lights out by 10:15\n")
MEAL = {"foods": ["grilled salmon", "rice", "broccoli"], "protein": 42, "fat": 18,
        "carbs": 45, "calories": 510, "omega3": 1800, "summary": "Salmon rice bowl (mock)"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Access-Control-Allow-Headers", "content-type, ngrok-skip-browser-warning")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/tags":
            return self._json({"models": [{"name": "mock-chat:latest", "size": 2},
                                          {"name": "mock-vision:latest", "size": 1}]})
        self.send_error(404)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path != "/api/chat":
            return self.send_error(404)
        print("chat model=%s stream=%s msgs=%d images=%s" % (
            req.get("model"), req.get("stream"), len(req.get("messages", [])),
            any("images" in m for m in req.get("messages", []))), flush=True)
        if not req.get("stream"):
            content = json.dumps(MEAL) if req.get("format") == "json" else REPLY
            return self._json({"message": {"role": "assistant", "content": content}, "done": True})
        lines = [json.dumps({"message": {"role": "assistant", "content": REPLY[i:i + 7]}, "done": False}) + "\n"
                 for i in range(0, len(REPLY), 7)]
        lines.append(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}) + "\n")
        payload = "".join(lines).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for i in range(0, len(payload), 53):  # 53 bytes: never lines up with a line break
            chunk = payload[i:i + 53]
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
            time.sleep(0.03)
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11435
    print("mock ollama on http://127.0.0.1:%d" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
