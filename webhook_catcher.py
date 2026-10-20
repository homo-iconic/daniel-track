#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import signal
import urllib.request
import subprocess
import time
from datetime import datetime
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2

LOG = Path.home() / "track" / "master.jsonl"
TRACK = Path.home() / "track"
ALERT_BIN = TRACK / "motion_alert"
ALERT_PID = TRACK / "motion_alert.pid"
NTFY_TOPIC = "ZttLoc"

HOME_LAT = 34.102029
HOME_LON = -118.346478
HOME_RADIUS_M = 30.0

last = {}
last_change = {}
last_push = 0

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

def is_home(lat, lon):
    return haversine_m(lat, lon, HOME_LAT, HOME_LON) <= HOME_RADIUS_M

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

def mac_motion_alert(title, message):
    """Top-right Mac panel; Submarine chime loops until Dismiss."""
    try:
        if not ALERT_BIN.exists():
            print("  mac alert skipped: motion_alert binary missing (swiftc motion_alert.swift)")
            return
        if ALERT_PID.exists():
            try:
                os.kill(int(ALERT_PID.read_text().strip()), signal.SIGTERM)
            except Exception:
                pass
            try:
                ALERT_PID.unlink(missing_ok=True)
            except Exception:
                pass
            time.sleep(0.2)
        subprocess.Popen(
            [str(ALERT_BIN), title, message],
            cwd=str(TRACK),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"  → mac alert: {title}")
    except Exception as e:
        print(f"  mac alert failed: {e}")

def rebuild_and_push():
    global last_push
    try:
        track = Path.home() / "track"
        subprocess.run(["python3", "clip_processor.py"], cwd=track, check=True)
        subprocess.run(["git", "add", "clips.json", "all_points.json"], cwd=track, check=True)
        # Always commit (even if identical) so the site gets a heartbeat
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"auto-update {datetime.now().isoformat()}"],
            cwd=track, check=True
        )
        subprocess.run(["git", "push"], cwd=track, check=True)
        print("  → pushed to GitHub Pages")
        last_push = time.time()
    except Exception as e:
        print(f"  rebuild/push failed: {e}")
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

        home_flag = None
        if lat is not None and lon is not None:
            home_flag = is_home(float(lat), float(lon))

        stale_min = 0.0
        is_stale = False
        if lat is not None and lon is not None and "daniel" in name:
            latf, lonf = float(lat), float(lon)
            prev = last_change.get("daniel")
            if prev is None:
                last_change["daniel"] = (latf, lonf, now)
            else:
                prev_lat, prev_lon, prev_ts = prev
                dist_since = haversine_m(prev_lat, prev_lon, latf, lonf)
                if dist_since > 15:
                    last_change["daniel"] = (latf, lonf, now)
                else:
                    stale_min = (now - prev_ts).total_seconds() / 60.0
                    is_stale = stale_min >= 5

        entry = {
            "ts": now_s,
            "name": name,
            "lat": lat,
            "lon": lon,
            "accuracy": acc,
            "home": home_flag,
            "stale": is_stale,
            "stale_min": round(stale_min, 1) if is_stale else 0,
            "raw": data
        }
        with LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")

        stale_txt = ""
        if is_stale:
            stale_txt = f"  \033[91mSTALE for {stale_min:.0f} min\033[0m"
        print(f"\n{now_s}  {name}  {lat},{lon}  acc={acc}  home={home_flag}{stale_txt}")

        if "daniel" in name and lat is not None and lon is not None:
            lat, lon = float(lat), float(lon)
            acc = float(acc) if acc is not None else None
            currently_home = home_flag
            prev = last.get("daniel")

            if prev:
                plat, plon, pts, pacc, prev_home = prev
                dist = haversine_m(plat, plon, lat, lon)
                dt = (now - pts).total_seconds()
                speed = (dist / dt) if dt > 0 else 0

                print(f"  moved {dist:.1f} m in {dt:.0f}s  ({speed:.1f} m/s)  prev_home={prev_home} curr_home={currently_home}")

                if prev_home and not currently_home:
                    msg = f"{dist:.0f} m ({dist*3.28:.0f} ft)\n{lat:.5f},{lon:.5f}"
                    ntfy("Left home", msg)
                    mac_motion_alert("Left home", f"Moved {dist:.0f} m · {lat:.5f}, {lon:.5f}")

                if dist >= 5 and not (prev_home and currently_home):
                    msg = f"{dist:.0f} m ({dist*3.28:.0f} ft)\n{lat:.5f},{lon:.5f}"
                    ntfy("Daniel moved", msg)
                    mac_motion_alert(
                        "Motion detected",
                        f"Daniel moved {dist:.0f} m ({dist*3.28:.0f} ft)\n{lat:.5f}, {lon:.5f}",
                    )

                if dist > 100 and speed > 30:
                    ntfy("Possible spoof", f"Jump {dist:.0f} m at {speed:.0f} m/s")

                if pacc and acc and acc > pacc * 3 and acc > 50:
                    ntfy("Accuracy drop", f"Accuracy {pacc:.0f}m → {acc:.0f}m")

            if acc and acc > 100:
                ntfy("Low accuracy", f"Accuracy {acc:.0f} m")

            last["daniel"] = (lat, lon, now, acc, currently_home)

            # Terminal print is instant. The public site only updates after rebuild+git push
            # + GitHub Actions (~30–60s). Throttle pushes to at most once per 30s.
            since = time.time() - last_push
            if since > 30 or last_push == 0:
                rebuild_and_push()
            else:
                print(f"  → logged locally; next site push in {30 - since:.0f}s")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        return

print(f"Logging to {LOG}")
print(f"ntfy: https://ntfy.sh/{NTFY_TOPIC}")
print(f"Home geofence: {HOME_LAT},{HOME_LON} ±{HOME_RADIUS_M}m")
print("Listening on http://127.0.0.1:8080")
HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
