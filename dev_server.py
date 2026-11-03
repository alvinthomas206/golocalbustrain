"""Local GoLocal web server and small NTES Client adapter."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import math
import heapq
import threading
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8000
BUS_DATA_DIR = ROOT / "bus-data"
BUS_DATA_FILES = (
    "alappuzha.json", "attingal.json", "ernakulam.json", "idukki.json", "kannur.json",
    "kottayam.json", "kozhikkode.json", "malappuram.json", "muvattupuzha.json",
    "palakkad-1.json", "palakkad-2.json", "pathanamthitta.json", "vadakara.json", "wayanad.json",
)
BUS_DATA_URL = "https://raw.githubusercontent.com/amith-vp/Kerala-Private-Bus-Timing/main/"
BUS_DATA_TTL = 6 * 60 * 60
BUS_DATA_LOCK = threading.Lock()
BUS_DATA_CACHE = None
FIREBASE_BUS_APP_NAME = "golocal-bus-data"
TRAIN_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
CACHE_LOCK = threading.Lock()
CACHE_TTL = 300
TRANSFER_HUBS = (
    "NDLS", "GZB", "AGC", "CNB", "LKO", "JHS", "KOTA", "BPL", "NGP", "BSB", "PNBE",
    "HWH", "KGP", "BRC", "ADI", "ST", "MMCT", "KYN", "PUNE", "SC", "HYB", "MAS", "SBC",
    "ERS", "ERN", "KTYM", "ALLP", "QLN", "AWY", "SRR", "CLT", "TCR", "PGT", "CNGR",
    "KYJ", "TVC", "TVCN", "VSKP", "BBS", "ROU", "TATA", "JP", "PTK", "ASR", "LTT",
    "DLI", "NZM", "DEE", "ANVT", "BVI", "BRC", "KGP", "JUC", "BZA", "GNT",
)


def client():
    try:
        from ntes import NTESClient
    except ModuleNotFoundError as exc:
        if exc.name == "ntes":
            raise RuntimeError("NTES Client dependency is missing from this deployment. Install dependencies from requirements.txt or pyproject.toml.") from exc
        raise RuntimeError(f"NTES Client dependency is incomplete; missing Python module '{exc.name}'.") from exc
    except ImportError as exc:
        raise RuntimeError(f"NTES Client could not be imported: {exc}") from exc
    # Keep cloud function requests bounded; an NTES connection can stall longer
    # from serverless IP ranges than it does from a local network.
    return NTESClient(timeout=8, retries=1)


def api_trains(from_code: str, to_code: str) -> dict:
    cache_key = (from_code, to_code)
    with CACHE_LOCK:
        cached = TRAIN_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1]
    result = client().trains_between(from_code, to_code)
    if isinstance(result, dict) and result.get("errorMessage"):
        raise RuntimeError(result["errorMessage"])
    trains = result.get("Trains", []) if isinstance(result, dict) else []
    mapped = []
    for item in trains:
        mapped.append({
            "number": str(item.get("TrainNumber", "")),
            "name": item.get("TrainName", "Train"),
            "days": item.get("DayOfRun", ""),
            "type": item.get("TrainTypeDesc") or item.get("TrainType") or "Train",
            "fromCode": item.get("FromStation", from_code),
            "toCode": item.get("toStation") or item.get("ToStation") or to_code,
            "fromName": item.get("FromStationName") or result.get("StationFrom", from_code),
            "toName": item.get("ToStationName") or result.get("StationTo", to_code),
            "depart": item.get("DepTimeFrom", ""),
            "arrive": item.get("ArrTimeTo", ""),
            "duration": item.get("TravelTime", ""),
            "classes": item.get("ClassOfTravel", ""),
        })
    response = {"from": from_code, "to": to_code, "count": len(mapped), "trains": mapped}
    with CACHE_LOCK:
        TRAIN_CACHE[cache_key] = (time.monotonic(), response)
    return response


def station_coordinates() -> dict[str, tuple[float, float]]:
    try:
        records = json.loads((ROOT / "stations.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    coords = {}
    for station in records:
        location = station.get("coordinates") or {}
        try:
            coords[str(station.get("code", "")).upper()] = (
                float(location["latitude"]), float(location["longitude"])
            )
        except (KeyError, TypeError, ValueError):
            continue
    return coords


def nearby_hubs(from_code: str, to_code: str, limit: int = 8) -> list[str]:
    coords = station_coordinates()
    origin, destination = coords.get(from_code), coords.get(to_code)
    eligible = [code for code in TRANSFER_HUBS if code not in (from_code, to_code) and code in coords]
    if not origin or not destination:
        return eligible[:limit]
    mean_lat = math.radians((origin[0] + destination[0]) / 2)
    def xy(point: tuple[float, float]) -> tuple[float, float]:
        return point[1] * math.cos(mean_lat), point[0]
    ox, oy = xy(origin); dx, dy = xy(destination)
    vx, vy = dx - ox, dy - oy
    length2 = vx * vx + vy * vy
    if length2 < 0.0001:
        return eligible[:limit]
    ranked = []
    for code in eligible:
        px, py = xy(coords[code])
        t = ((px - ox) * vx + (py - oy) * vy) / length2
        if not -0.05 < t < 1.05:
            continue
        distance = math.hypot(px - (ox + t * vx), py - (oy + t * vy))
        ranked.append((distance, code))
    ranked.sort()
    corridor = [code for _, code in ranked[:6]]

    def nearest(point: tuple[float, float], count: int = 3) -> list[str]:
        px, py = xy(point)
        candidates = []
        for code in eligible:
            hx, hy = xy(coords[code])
            distance = math.hypot(hx - px, hy - py)
            if distance <= 1.35:
                candidates.append((distance, code))
        candidates.sort()
        return [code for _, code in candidates[:count]]

    # Nearby hubs let a short local train bring passengers from a small station
    # such as Chingavanam to an intercity boarding station such as Kottayam.
    choices = nearest(origin) + corridor + nearest(destination)
    ordered = list(dict.fromkeys(code for code in choices if code not in (from_code, to_code)))
    return ordered[:max(limit, 12)] or eligible[:max(limit, 12)]


def nearby_access_stations(station_code: str, count: int = 3) -> list[str]:
    coords = station_coordinates()
    point = coords.get(station_code)
    if not point:
        return []
    lat_scale = math.cos(math.radians(point[0]))
    eligible = [code for code in TRANSFER_HUBS if code != station_code and code in coords]
    ranked = []
    for code in eligible:
        lat, lon = coords[code]
        distance = math.hypot((lon - point[1]) * lat_scale, lat - point[0])
        if distance <= 0.8:  # approximately 85 km, allowing a practical feeder station
            ranked.append((distance, code))
    ranked.sort()
    return [code for _, code in ranked[:count]]


def parse_clock(value: str):
    try:
        hour, minute = str(value).strip().split(":")[:2]
        return int(hour), int(minute)
    except (ValueError, TypeError):
        return None


def duration_minutes(value: str):
    parsed = parse_clock(value)
    return parsed[0] * 60 + parsed[1] if parsed else None


def runs_on(day_text: str, day: datetime) -> bool:
    days = str(day_text or "").lower()
    if not days or "daily" in days or "all days" in days:
        return True
    weekday = day.strftime("%a").lower()
    return weekday in days or day.strftime("%A").lower() in days


def connection_plans(from_code: str, to_code: str, journey_date: str, via_code: str = "") -> dict:
    try:
        start_date = datetime.strptime(journey_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Choose a valid journey date.") from exc
    if via_code and via_code in (from_code, to_code):
        raise ValueError("The mid-stop must differ from the origin and destination.")
    if via_code:
        return plans_through_via(from_code, to_code, journey_date, start_date, via_code)
    hubs = [via_code] if via_code else nearby_hubs(from_code, to_code)
    routes: dict[tuple[str, str], list[dict]] = {}
    pairs = {(from_code, hub) for hub in hubs} | {(hub, to_code) for hub in hubs}
    def fetch(pair):
        try:
            return pair, api_trains(*pair).get("trains", [])
        except Exception:
            return pair, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, pair) for pair in pairs]
        for future in as_completed(futures):
            pair, trains = future.result()
            routes[pair] = trains

    plans = []
    for hub in hubs:
        first_options = routes.get((from_code, hub), [])
        second_options = routes.get((hub, to_code), [])
        for first in first_options:
            first_depart = parse_clock(first.get("depart"))
            first_duration = duration_minutes(first.get("duration"))
            if not first_depart or first_duration is None or not runs_on(first.get("days"), start_date):
                continue
            depart_at = start_date.replace(hour=first_depart[0], minute=first_depart[1])
            arrive_hub = depart_at + timedelta(minutes=first_duration)
            for second in second_options:
                if first.get("number") == second.get("number"):
                    continue
                second_depart = parse_clock(second.get("depart"))
                second_duration = duration_minutes(second.get("duration"))
                if not second_depart or second_duration is None:
                    continue
                for day_offset in range(3):
                    service_date = arrive_hub.date() + timedelta(days=day_offset)
                    service_dt = datetime.combine(service_date, datetime.min.time())
                    if not runs_on(second.get("days"), service_dt):
                        continue
                    leave_hub = service_dt.replace(hour=second_depart[0], minute=second_depart[1])
                    wait = (leave_hub - arrive_hub).total_seconds() / 60
                    if 45 <= wait <= 18 * 60:
                        arrive_destination = leave_hub + timedelta(minutes=second_duration)
                        plans.append({
                            "hub": hub,
                            "waitMinutes": int(wait),
                            "totalMinutes": int((arrive_destination - depart_at).total_seconds() / 60),
                            "departAt": depart_at.isoformat(),
                            "arriveAt": arrive_destination.isoformat(),
                            "first": first,
                            "second": second,
                        })
                        break
    plans.sort(key=lambda plan: (plan["totalMinutes"], plan["departAt"]))
    # Keep one option per train pair and transfer station, then show a manageable set.
    unique, seen = [], set()
    for plan in plans:
        key = (plan["first"].get("number"), plan["second"].get("number"), plan["hub"])
        if key in seen:
            continue
        seen.add(key); unique.append(plan)
        if len(unique) == 12:
            break
    return {"from": from_code, "to": to_code, "date": journey_date, "count": len(unique), "plans": unique}


def plans_through_via(from_code: str, to_code: str, journey_date: str, start_date: datetime, via_code: str) -> dict:
    access_options = [from_code, *nearby_access_stations(from_code)]
    unique_paths = []
    for access_code in access_options:
        path = list(dict.fromkeys((from_code, access_code, via_code, to_code)))
        if len(path) >= 3 and path not in unique_paths:
            unique_paths.append(path)

    route_cache: dict[tuple[str, str], list[dict]] = {}
    pairs = {(a, b) for path in unique_paths for a, b in zip(path, path[1:])}
    def fetch(pair):
        try:
            return pair, api_trains(*pair).get("trains", [])
        except Exception:
            return pair, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, pair) for pair in pairs]
        for future in as_completed(futures):
            pair, trains = future.result()
            route_cache[pair] = trains

    plans = []
    for path in unique_paths:
        leg_options = [route_cache.get(pair, []) for pair in zip(path, path[1:])]
        if any(not options for options in leg_options):
            continue
        def extend(index: int, previous_arrival: datetime | None, selected_legs: list[dict], transfers: list[dict]):
            if len(plans) >= 250:
                return
            if index == len(leg_options):
                departure = datetime.fromisoformat(selected_legs[0]["departAt"])
                arrival = datetime.fromisoformat(selected_legs[-1]["arriveAt"])
                plans.append({
                    "via": via_code,
                    "legs": selected_legs.copy(),
                    "transfers": transfers.copy(),
                    "transferCount": len(transfers),
                    "totalMinutes": int((arrival - departure).total_seconds() / 60),
                    "departAt": departure.isoformat(),
                    "arriveAt": arrival.isoformat(),
                })
                return
            for train in leg_options[index][:35]:
                depart_time = parse_clock(train.get("depart"))
                duration = duration_minutes(train.get("duration"))
                if not depart_time or duration is None:
                    continue
                if index == 0:
                    service_date = start_date
                    if not runs_on(train.get("days"), service_date):
                        continue
                    leave = service_date.replace(hour=depart_time[0], minute=depart_time[1])
                else:
                    leave = None
                    for offset in range(3):
                        service_date = previous_arrival.date() + timedelta(days=offset)
                        if not runs_on(train.get("days"), datetime.combine(service_date, datetime.min.time())):
                            continue
                        possible = datetime.combine(service_date, datetime.min.time()).replace(hour=depart_time[0], minute=depart_time[1])
                        wait = (possible - previous_arrival).total_seconds() / 60
                        if 45 <= wait <= 18 * 60:
                            leave = possible
                            break
                    if leave is None:
                        continue
                    transfers.append({
                        "station": path[index],
                        "waitMinutes": int((leave - previous_arrival).total_seconds() / 60),
                    })
                arrival = leave + timedelta(minutes=duration)
                selected_legs.append({
                    **train,
                    "fromCode": path[index],
                    "toCode": path[index + 1],
                    "departAt": leave.isoformat(),
                    "arriveAt": arrival.isoformat(),
                })
                extend(index + 1, arrival, selected_legs, transfers)
                selected_legs.pop()
                if index > 0:
                    transfers.pop()

        extend(0, None, [], [])

    plans.sort(key=lambda plan: (plan["totalMinutes"], plan["departAt"]))
    unique, seen = [], set()
    for plan in plans:
        key = tuple((leg.get("number"), leg.get("fromCode"), leg.get("toCode")) for leg in plan["legs"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(plan)
        if len(unique) == 12:
            break
    return {"from": from_code, "to": to_code, "via": via_code, "date": journey_date, "count": len(unique), "plans": unique}


def api_schedule(train_no: str) -> dict:
    result = client().schedule(train_no)
    if isinstance(result, dict) and result.get("errorMessage"):
        raise RuntimeError(result["errorMessage"])
    stops = result.get("stations", []) if isinstance(result, dict) else []
    mapped = [{
        "code": item.get("StationCode", ""),
        "name": item.get("StationName", ""),
        "arrival": item.get("STA", ""),
        "departure": item.get("STD", ""),
    } for item in stops]
    return {"train": train_no, "duration": result.get("TravelTime", ""), "days": result.get("DaysOfRun", ""), "stations": mapped}


def api_status(train_no: str, iso_date: str) -> dict:
    try:
        journey_date = datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d-%b-%Y")
    except ValueError as exc:
        raise ValueError("Choose a valid journey date.") from exc
    result = client().live_status(train_no, journey_date)
    if isinstance(result, dict) and result.get("errorMessage"):
        raise RuntimeError(result["errorMessage"])
    return {
        "train": train_no,
        "position": result.get("LastUpdate") or result.get("CurrentStationName") or "Status received",
        "station": result.get("CurrentStationName") or result.get("CurrentStation", ""),
        "next": result.get("NextStationName") or result.get("NextStationCode", ""),
        "updated": result.get("LastUpdate", ""),
    }


def normalize_bus_stop(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", canonical_bus_stop_name(folded).lower())


def canonical_bus_stop_name(value: str) -> str:
    """Collapse spelling and old/new bus-stand variants into the parent location."""
    label = re.sub(r"\s+", " ", str(value or "").strip())
    label = re.sub(r"\s+(?:(?:OLD|NEW)\s+)?BUS\s*(?:STAND|ST[ .]?AND|TAND)$", "", label, flags=re.IGNORECASE)
    label = re.sub(r"\s+", " ", label).strip(" ,.-")
    compact = re.sub(r"[^a-z0-9]+", "", label.casefold())
    aliases = {"changanacherry": "CHANGANASSERY", "kothamangalam": "KOTTAMANGALAM"}
    if compact in aliases:
        return aliases[compact]
    return label


def parse_bus_clock(value: str):
    text = re.sub(r"\s+", " ", str(value or "").strip().lower()).replace("a.m.", "am").replace("p.m.", "pm")
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            pass
    return None


def load_bus_timetable():
    """Load and index the public Kerala private-bus timetable, caching it in this process."""
    global BUS_DATA_CACHE
    with BUS_DATA_LOCK:
        firebase_client = firebase_bus_firestore_client()
        firebase_revision = None
        if firebase_client:
            try:
                metadata = firebase_client.collection("busMetadata").document("state").get()
                firebase_revision = int((metadata.to_dict() or {}).get("revision", 0))
            except Exception as exc:
                raise RuntimeError(f"Could not check the Firebase bus catalogue version: {exc}") from exc
        cache_revision = BUS_DATA_CACHE[4] if BUS_DATA_CACHE and len(BUS_DATA_CACHE) > 4 else None
        if BUS_DATA_CACHE and time.monotonic() - BUS_DATA_CACHE[0] < BUS_DATA_TTL and cache_revision == firebase_revision:
            return BUS_DATA_CACHE[1], BUS_DATA_CACHE[2], BUS_DATA_CACHE[3]

        def fetch_file(filename: str):
            local_path = BUS_DATA_DIR / filename
            if local_path.is_file():
                return filename, json.loads(local_path.read_text(encoding="utf-8"))
            request = urllib.request.Request(
                BUS_DATA_URL + filename,
                headers={"User-Agent": "GoLocal student journey planner"},
            )
            with urllib.request.urlopen(request, timeout=25) as response:
                raw = response.read()
            try:
                BUS_DATA_DIR.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(raw)
            except OSError:
                pass  # Vercel functions have a read-only deployment bundle.
            return filename, json.loads(raw.decode("utf-8"))

        if firebase_client:
            try:
                collection = firebase_client.collection("busVehicles")
                bus_records = []
                for document in collection.stream():
                    bus_data = document.to_dict() or {}
                    schedule = []
                    for trip_index, trip in enumerate(bus_data.get("trips") or []):
                        stations = [{
                            "station": stop.get("station") or stop.get("name") or "",
                            "arrivalTime": stop.get("arrivalTime") or stop.get("arrival") or "",
                            "departureTime": stop.get("departureTime") or stop.get("departure") or "",
                        } for stop in trip.get("stops") or []]
                        if len(stations) >= 2:
                            schedule.append({"trip": trip.get("tripNo", trip_index + 1), "stations": stations})
                    if schedule:
                        bus_records.append({
                            "Vehicle Number": bus_data.get("vehicleNumber") or bus_data.get("vehicle_number") or document.id,
                            "Bus Name": bus_data.get("busName") or "",
                            "schedule": schedule,
                        })
                if not bus_records:
                    raise RuntimeError("Firebase has no bus records yet. Sign in to /admin.html and import the source timetable first.")
                files = {"firebase-firestore": {"busSchedules": bus_records}}
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Could not load bus records from Firebase: {exc}") from exc
        else:
            try:
                with ThreadPoolExecutor(max_workers=6) as pool:
                    files = dict(pool.map(fetch_file, BUS_DATA_FILES))
            except Exception as exc:
                raise RuntimeError(f"Could not load the Kerala bus timetable from bundled files or its GitHub source: {exc}") from exc

        runs, names, seen = [], {}, set()
        for filename, payload in files.items():
            schedules = payload.get("busSchedules", []) if isinstance(payload, dict) else []
            for bus_record in schedules:
                vehicle_number = str(bus_record.get("Vehicle Number") or bus_record.get("vehicle_number") or "Bus").strip()
                vehicle_name = str(bus_record.get("Bus Name") or bus_record.get("busName") or vehicle_number).strip()
                for trip_index, trip in enumerate(bus_record.get("schedule") or []):
                    raw_stops = trip.get("stations") or []
                    stops, previous_departure = [], None
                    for item in raw_stops:
                        label = canonical_bus_stop_name(item.get("station") or "")
                        key = normalize_bus_stop(label)
                        arrival = parse_bus_clock(item.get("arrivalTime"))
                        departure = parse_bus_clock(item.get("departureTime"))
                        if not key or arrival is None and departure is None:
                            continue
                        arrival = departure if arrival is None else arrival
                        departure = arrival if departure is None else departure
                        while arrival < (previous_departure if previous_departure is not None else 0):
                            arrival += 1440
                        while departure < arrival:
                            departure += 1440
                        while previous_departure is not None and departure < previous_departure:
                            departure += 1440
                            arrival += 1440
                        previous_departure = departure
                        names.setdefault(key, label)
                        stops.append({"key": key, "name": names[key], "arrival": arrival, "departure": departure})
                    if len(stops) < 2:
                        continue
                    signature = (vehicle_number, tuple((s["key"], s["arrival"], s["departure"]) for s in stops))
                    if signature in seen:
                        continue
                    seen.add(signature)
                    runs.append({"id": f"{filename}:{vehicle_number}:{trip_index}", "vehicle": vehicle_name, "vehicleNumber": vehicle_number, "stops": stops})

        if not runs:
            raise RuntimeError("The Kerala bus source returned no usable bus trips.")
        segment_ranges = {}
        for run in runs:
            for first_stop, next_stop in zip(run["stops"], run["stops"][1:]):
                key = (first_stop["key"], next_stop["key"])
                duration = next_stop["arrival"] - first_stop["departure"]
                stats = segment_ranges.setdefault(key, [0, duration, duration])
                stats[0] += 1
                stats[1] = min(stats[1], duration)
                stats[2] = max(stats[2], duration)
        suspect_segments = {
            key for key, (count, shortest, longest) in segment_ranges.items()
            if count >= 2 and shortest <= 2 and longest >= 10
        }
        for run in runs:
            invalid_prefix = [0]
            for first_stop, next_stop in zip(run["stops"], run["stops"][1:]):
                key = (first_stop["key"], next_stop["key"])
                duration = next_stop["arrival"] - first_stop["departure"]
                invalid = key in suspect_segments and duration <= 2
                invalid_prefix.append(invalid_prefix[-1] + int(invalid))
            run["invalidPrefix"] = invalid_prefix
        index = {}
        for run_index, run in enumerate(runs):
            for stop_index, stop in enumerate(run["stops"][:-1]):
                index.setdefault(stop["key"], []).append((run_index, stop_index))
        BUS_DATA_CACHE = (time.monotonic(), runs, index, names, firebase_revision)
        return runs, index, names


def bus_station_directory() -> dict:
    if os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip():
        _, _, firebase_names = load_bus_timetable()
        station_names = sorted({canonical_bus_stop_name(name) for name in firebase_names.values() if canonical_bus_stop_name(name)}, key=str.casefold)
        return {"count": len(station_names), "stations": station_names, "source": "Firebase bus catalogue"}
    index_path = BUS_DATA_DIR / "bus_stations.json"
    if index_path.is_file():
        try:
            cached = json.loads(index_path.read_text(encoding="utf-8"))
            names = sorted({canonical_bus_stop_name(name) for name in cached.get("stations", []) if canonical_bus_stop_name(name)}, key=str.casefold)
            return {**cached, "count": len(names), "stations": names}
        except (OSError, json.JSONDecodeError):
            pass
    _, _, names = load_bus_timetable()
    station_names = sorted({canonical_bus_stop_name(name) for name in names.values() if canonical_bus_stop_name(name)}, key=str.casefold)
    result = {"count": len(station_names), "stations": station_names, "source": "amith-vp/Kerala-Private-Bus-Timing"}
    try:
        index_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return result


def format_bus_clock(minutes: int) -> str:
    return (datetime(2000, 1, 1) + timedelta(minutes=minutes % 1440)).strftime("%I:%M %p")


def firebase_bus_firestore_client():
    service_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not service_json:
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
        try:
            firebase_app = firebase_admin.get_app(FIREBASE_BUS_APP_NAME)
        except ValueError:
            credential = credentials.Certificate(json.loads(service_json))
            firebase_app = firebase_admin.initialize_app(credential, name=FIREBASE_BUS_APP_NAME)
        return firestore.client(app=firebase_app)
    except Exception as exc:
        raise RuntimeError(f"Could not initialize the Firebase bus connection: {exc}") from exc


def bus_connection_plans(from_station: str, to_station: str, leave_after: str, max_transfers=None) -> dict:
    runs, index, names = load_bus_timetable()
    origin, destination = normalize_bus_stop(from_station), normalize_bus_stop(to_station)
    if origin not in names:
        raise ValueError(f"Choose a Kerala bus stop from the suggestions for the origin: {from_station}.")
    if destination not in names:
        raise ValueError(f"Choose a Kerala bus stop from the suggestions for the destination: {to_station}.")
    if origin == destination:
        raise ValueError("Choose two different bus stops.")
    try:
        start_minute = parse_bus_clock(leave_after)
    except Exception:
        start_minute = None
    if start_minute is None:
        raise ValueError("Choose a valid leave-after time.")

    unlimited = max_transfers is None
    if not unlimited:
        try:
            max_transfers = max(0, int(max_transfers))
        except (TypeError, ValueError) as exc:
            raise ValueError("Enter a non-negative transfer limit or choose no limit.") from exc

    def find_best_plan(search_after: int):
        start_state = origin if unlimited else (origin, -1)
        best = {start_state: search_after}
        parent, transfer_labels = {}, {start_state: -1}
        queue = [(search_after, start_state)]
        target_states = []
        horizon = search_after + 24 * 60

        while queue:
            arrival_at_station, state = heapq.heappop(queue)
            if arrival_at_station != best.get(state):
                continue
            station = state if unlimited else state[0]
            transfers_so_far = transfer_labels[state]
            visited_stations = {station}
            cursor = state
            while cursor != start_state:
                previous, previous_leg = parent[cursor]
                visited_stations.add(normalize_bus_stop(previous_leg["from"]))
                cursor = previous
            if station == destination:
                target_states.append(state)
                if unlimited:
                    break
                continue
            for run_index, stop_index in index.get(station, []):
                run = runs[run_index]
                first_stop = run["stops"][stop_index]
                ready_at = arrival_at_station + (5 if transfers_so_far >= 0 else 0)
                scheduled_departure = first_stop["departure"]
                if scheduled_departure < ready_at:
                    scheduled_departure += ((ready_at - scheduled_departure + 1439) // 1440) * 1440
                shift = scheduled_departure - first_stop["departure"]
                if scheduled_departure > horizon:
                    continue
                next_transfers = 0 if transfers_so_far < 0 else transfers_so_far + 1
                if not unlimited and next_transfers > max_transfers:
                    continue
                for next_stop_index, next_stop in enumerate(run["stops"][stop_index + 1:], start=stop_index + 1):
                    if run["invalidPrefix"][next_stop_index] > run["invalidPrefix"][stop_index]:
                        continue
                    next_arrival = next_stop["arrival"] + shift
                    while next_arrival < scheduled_departure:
                        next_arrival += 1440
                    if next_arrival > horizon or next_stop["key"] in visited_stations:
                        continue
                    next_state = next_stop["key"] if unlimited else (next_stop["key"], next_transfers)
                    if next_arrival >= best.get(next_state, float("inf")):
                        continue
                    leg = {
                        "vehicle": run["vehicle"],
                        "vehicleNumber": run.get("vehicleNumber", run["vehicle"]),
                        "from": first_stop["name"],
                        "to": next_stop["name"],
                        "departMinute": scheduled_departure,
                        "arriveMinute": next_arrival,
                        "departDayOffset": scheduled_departure // 1440,
                        "arriveDayOffset": next_arrival // 1440,
                    }
                    best[next_state] = next_arrival
                    parent[next_state] = (state, leg)
                    transfer_labels[next_state] = next_transfers
                    heapq.heappush(queue, (next_arrival, next_state))

        if not target_states:
            return None
        target = min(target_states, key=lambda state: (best[state], transfer_labels[state]))
        legs = []
        cursor = target
        while cursor != start_state:
            previous, leg = parent[cursor]
            legs.append(dict(leg))
            cursor = previous
        legs.reverse()
        if not legs:
            return None
        first_departure = legs[0]["departMinute"]
        arrival_minute = best[target]
        waits = []
        for previous_leg, next_leg in zip(legs, legs[1:]):
            waits.append({
                "station": previous_leg["to"],
                "waitMinutes": max(0, next_leg["departMinute"] - previous_leg["arriveMinute"]),
            })
        for leg in legs:
            leg["depart"] = format_bus_clock(leg.pop("departMinute"))
            leg["arrive"] = format_bus_clock(leg.pop("arriveMinute"))
        return {
            "transferCount": len(legs) - 1,
            "totalMinutes": arrival_minute - first_departure,
            "departAt": format_bus_clock(first_departure),
            "arriveAt": format_bus_clock(arrival_minute),
            "arrivalDayOffset": arrival_minute // 1440,
            "legs": legs,
            "transfers": waits,
            "_firstDeparture": first_departure,
            "_arrivalMinute": arrival_minute,
        }

    # Return up to three fast journeys, each based on a distinct first departure.
    plans, seen_departures = [], set()
    search_after = start_minute
    for _ in range(3):
        plan = find_best_plan(search_after)
        if not plan:
            break
        first_departure = plan.pop("_firstDeparture")
        if first_departure in seen_departures:
            break
        seen_departures.add(first_departure)
        plans.append(plan)
        search_after = first_departure + 1
    plans.sort(key=lambda plan: (plan["totalMinutes"], plan["_arrivalMinute"], plan["transferCount"]))
    for plan in plans:
        plan.pop("_arrivalMinute", None)
    return {
        "from": names[origin], "to": names[destination], "leaveAfter": format_bus_clock(start_minute),
        "maxTransfers": "unlimited" if unlimited else max_transfers,
        "count": len(plans), "plans": plans,
        "source": "https://github.com/amith-vp/Kerala-Private-Bus-Timing",
    }


class GoLocalHandler(BaseHTTPRequestHandler):
    server_version = "GoLocal/0.2"

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                self.send_json(200, {"ok": True})
                return
            if path == "/api/buses":
                if query.get("action", [""])[0] == "stations":
                    self.send_json(200, bus_station_directory())
                    return
                from_station = query.get("from", [""])[0]
                to_station = query.get("to", [""])[0]
                leave_after = query.get("leaveAfter", ["06:00"])[0]
                transfer_value = query.get("maxTransfers", ["3"])[0]
                if not from_station.strip() or not to_station.strip():
                    self.send_json(400, {"error": "Choose an origin and destination bus stop."})
                    return
                max_transfers = None if transfer_value.lower() == "unlimited" else transfer_value
                self.send_json(200, bus_connection_plans(from_station, to_station, leave_after, max_transfers))
                return
            if path == "/api/trains":
                from_code = query.get("from", [""])[0].upper()
                to_code = query.get("to", [""])[0].upper()
                if not re.fullmatch(r"[A-Z]{2,5}", from_code) or not re.fullmatch(r"[A-Z]{2,5}", to_code):
                    self.send_json(400, {"error": "Enter valid station codes, such as NDLS and MMCT."})
                    return
                self.send_json(200, api_trains(from_code, to_code))
                return
            if path == "/api/connections":
                from_code = query.get("from", [""])[0].upper()
                to_code = query.get("to", [""])[0].upper()
                journey_date = query.get("date", [datetime.now().strftime("%Y-%m-%d")])[0]
                via_code = query.get("via", [""])[0].upper()
                if not re.fullmatch(r"[A-Z]{2,5}", from_code) or not re.fullmatch(r"[A-Z]{2,5}", to_code):
                    self.send_json(400, {"error": "Enter valid station codes, such as NDLS and MMCT."})
                    return
                if via_code and not re.fullmatch(r"[A-Z]{2,5}", via_code):
                    self.send_json(400, {"error": "Enter a valid mid-stop station code."})
                    return
                self.send_json(200, connection_plans(from_code, to_code, journey_date, via_code))
                return
            if path == "/api/schedule" or path.startswith("/api/schedule/"):
                train_no = query.get("trainNo", [""])[0] or path.removeprefix("/api/schedule/")
                if not re.fullmatch(r"\d{4,6}", train_no):
                    self.send_json(400, {"error": "Invalid train number."})
                    return
                self.send_json(200, api_schedule(train_no))
                return
            if path == "/api/status" or path.startswith("/api/status/"):
                train_no = query.get("trainNo", [""])[0] or path.removeprefix("/api/status/")
                if not re.fullmatch(r"\d{4,6}", train_no):
                    self.send_json(400, {"error": "Invalid train number."})
                    return
                journey_date = query.get("date", [datetime.now().strftime("%Y-%m-%d")])[0]
                self.send_json(200, api_status(train_no, journey_date))
                return

            relative = "index.html" if path == "/" else path.lstrip("/")
            target = (ROOT / relative).resolve()
            if ROOT not in target.parents or not target.is_file():
                self.send_json(404, {"error": "Page not found."})
                return
            body = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
                content_type += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (RuntimeError, ValueError) as exc:
            self.send_json(503 if isinstance(exc, RuntimeError) else 400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(502, {"error": f"Could not load journey information: {exc}"})

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == "__main__":
    print(f"GoLocal is available at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop the local server.")
    ThreadingHTTPServer((HOST, PORT), GoLocalHandler).serve_forever()
