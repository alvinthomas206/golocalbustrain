from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dev_server import api_schedule


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        train_no = parse_qs(urlparse(self.path).query).get("trainNo", [""])[0]
        if not re.fullmatch(r"\d{4,6}", train_no):
            self.respond(400, {"error": "Invalid train number."})
            return
        try:
            self.respond(200, api_schedule(train_no))
        except Exception as exc:
            self.respond(502, {"error": f"NTES did not return the schedule: {exc}"})

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
