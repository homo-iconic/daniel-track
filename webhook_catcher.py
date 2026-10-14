#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import urllib.request
from datetime import datetime
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2

LOG = Path.home() / "track" / "master.jsonl"
NTFY_TOPIC = "ZttLoc"
last = {}  # name -> (lat, lon, ts, acc)

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

def ntfy(title, message):
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode(),
            headers={"Title": title, "Priority": "high", "Tags": "warning"}
        )
        urllib.request.urlopen(req, timeout=10)
        print(f"  → ntfy: {title}")
    except Exception as e:
        print(f"  ntfy failed: {e}")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"message":"API running."}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        name = (data.get("dev_id") or data.get("host_name") or "").lower()
        gps = data.get("gps") or []
        lat = gps[0] if len(gps) > 0 else None
        lon = gps[1] if len(gps) > 1 else None
        acc = data.get("gps_accuracy")

        now = datetime.now()
        now_s = now.isoformat()
        entry = {"ts": now_s, "name": name, "lat": lat, "lon": lon, "accuracy": acc, "raw": data}
        with LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")

        print(f"\n{now_s}  {name}  {lat},{lon}  acc={acc}")

        if "daniel" in name and lat is not None and lon is not None:
            lat, lon = float(lat), float(lon)
            acc = float(acc) if acc is not None else None
            prev = last.get("daniel")

            if prev:
                plat, plon, pts, pacc = prev
                dist = haversine_m(plat, plon, lat, lon)
                dt = (now - pts).total_seconds()
                speed = (dist / dt) if dt > 0 else 0  # m/s

                print(f"  moved {dist:.1f} m in {dt:.0f}s  ({speed:.1f} m/s)")

                # Very sensitive movement (≈10–20 ft)
                if dist >= 5:
                    ntfy("Daniel moved", f"{dist:.0f} m ({dist*3.28:.0f} ft)\n{lat:.5f},{lon:.5f}")

                # Possible teleport / spoof
                if dist > 100 and speed > 30:  # >100 m and >30 m/s (~67 mph)
                    ntfy("Possible spoof", f"Jump {dist:.0f} m at {speed:.0f} m/s")

                # Accuracy suddenly worse
                if pacc and acc and acc > pacc * 3 and acc > 50:
                    ntfy("Accuracy drop", f"Accuracy {pacc:.0f}m → {acc:.0f}m")

            if acc and acc > 100:
                ntfy("Low accuracy", f"Accuracy {acc:.0f} m")

            last["daniel"] = (lat, lon, now, acc)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        return

print(f"Logging to {LOG}")
print(f"ntfy: https://ntfy.sh/{NTFY_TOPIC}")
print("Listening on http://127.0.0.1:8080")
HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
