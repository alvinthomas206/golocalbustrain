from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dev_server import bus_connection_plans, bus_station_directory


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        try:
            if query.get("action", [""])[0] == "stations":
                self.respond(200, bus_station_directory())
                return
            from_station = query.get("from", [""])[0]
            to_station = query.get("to", [""])[0]
            leave_after = query.get("leaveAfter", ["06:00"])[0]
            transfer_value = query.get("maxTransfers", ["3"])[0]
            if not from_station.strip() or not to_station.strip():
                self.respond(400, {"error": "Choose an origin and destination bus stop."})
                return
            max_transfers = None if transfer_value.lower() == "unlimited" else transfer_value
            self.respond(200, bus_connection_plans(from_station, to_station, leave_after, max_transfers))
        except ValueError as exc:
            self.respond(400, {"error": str(exc)})
        except RuntimeError as exc:
            self.respond(503, {"error": str(exc)})
        except Exception as exc:
            self.respond(502, {"error": f"Could not load bus journey information: {exc}"})

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
