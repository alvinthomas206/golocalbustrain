from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dev_server import api_trains


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        from_code = query.get("from", [""])[0].upper()
        to_code = query.get("to", [""])[0].upper()
        if not re.fullmatch(r"[A-Z]{2,5}", from_code) or not re.fullmatch(r"[A-Z]{2,5}", to_code):
            self.respond(400, {"error": "Enter valid station codes."})
            return
        try:
            self.respond(200, api_trains(from_code, to_code))
        except Exception as exc:
            self.respond(502, {"error": f"NTES did not return train information: {exc}"})

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
