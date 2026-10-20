#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import signal
import threading
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

# No Daniel POST for this long → treat as Find My Pro stuck / offline
FIND_MY_SILENCE_SEC = 180

last = {}
last_change = {}
last_push = 0
last_daniel_post = time.time()  # don't alarm immediately on fresh start
last_silence_alert = 0

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

def alert_up():
    if not ALERT_PID.exists():
        return False
    try:
        os.kill(int(ALERT_PID.read_text().strip()), 0)
        return True
    except Exception:
        return False

def mac_alert(title, message, *, replace=True):
    """Top-right Mac panel; soft chime loops until Dismiss."""
    try:
        if not ALERT_BIN.exists():
            print("  mac alert skipped: motion_alert binary missing")
            return
        if alert_up() and not replace:
            print(f"  → mac alert already up: {title}")
            return
        if alert_up():
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

def payload_looks_like_error(data, lat, lon):
    """Find My Pro / FindMySyncPlus failed to produce usable coords."""
    if lat is None or lon is None:
        return True, "No coordinates in payload"
    try:
        latf, lonf = float(lat), float(lon)
    except (TypeError, ValueError):
        return True, "Coordinates were not numbers"
    if abs(latf) < 1e-6 and abs(lonf) < 1e-6:
        return True, "Coordinates were 0,0"
    if abs(latf) > 90 or abs(lonf) > 180:
        return True, "Coordinates out of range"

    err_bits = []
    for k, v in (data or {}).items():
        lk = str(k).lower()
        if any(x in lk for x in ("error", "fail", "status", "message", "reason")):
            err_bits.append(f"{k}={v}")
    blob = " ".join(err_bits).lower()
    if any(w in blob for w in ("error", "fail", "unable", "timeout", "denied", "unavailable", "no location")):
        return True, err_bits[0] if err_bits else "Error field in payload"

    gps = data.get("gps") if isinstance(data, dict) else None
    if gps is not None and not (isinstance(gps, (list, tuple)) and len(gps) >= 2):
        return True, "GPS field present but empty/invalid"

    return False, ""

def rebuild_and_push():
    global last_push
    try:
        subprocess.run(["python3", "clip_processor.py"], cwd=TRACK, check=True)
        subprocess.run(["git", "add", "clips.json", "all_points.json"], cwd=TRACK, check=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"auto-update {datetime.now().isoformat()}"],
            cwd=TRACK, check=True
        )
        subprocess.run(["git", "push"], cwd=TRACK, check=True)
        print("  → pushed to GitHub Pages")
        last_push = time.time()
    except Exception as e:
        print(f"  rebuild/push failed: {e}")

def silence_watch_loop():
    """If Find My stops posting, alarm until dismissed."""
    global last_silence_alert
    while True:
        time.sleep(30)
        age = time.time() - last_daniel_post
        if age < FIND_MY_SILENCE_SEC:
            continue
        # Don't stack if user already has the panel open
        if alert_up():
            continue
        # Re-nag at most every silence window after dismiss
        if time.time() - last_silence_alert < FIND_MY_SILENCE_SEC:
            continue
        mins = age / 60.0
        last_silence_alert = time.time()
        print(f"  → Find My silence: no post for {mins:.1f} min")
        mac_alert(
            "Find My silent",
            f"No Daniel update for {mins:.0f} min.\nFindMySyncPlus may be stuck or offline.",
            replace=False,
        )
        ntfy("Find My silent", f"No Daniel update for {mins:.0f} min")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"message":"API running."}')

    def do_POST(self):
        global last_daniel_post
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        name = (data.get("dev_id") or data.get("host_name") or "").lower()
        gps = data.get("gps") or []
        lat = gps[0] if isinstance(gps, (list, tuple)) and len(gps) > 0 else data.get("lat")
        lon = gps[1] if isinstance(gps, (list, tuple)) and len(gps) > 1 else data.get("lon")
        acc = data.get("gps_accuracy")

        now = datetime.now()
        now_s = now.isoformat()

        if "daniel" in name or "findmy" in name:
            last_daniel_post = time.time()

        home_flag = None
        if lat is not None and lon is not None:
            try:
                home_flag = is_home(float(lat), float(lon))
            except (TypeError, ValueError):
                home_flag = None

        stale_min = 0.0
        is_stale = False
        if lat is not None and lon is not None and "daniel" in name:
            try:
                latf, lonf = float(lat), float(lon)
            except (TypeError, ValueError):
                latf = lonf = None
            if latf is not None:
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

        # Find My Pro / Sync errors or missing coords
        if "daniel" in name or "findmy" in name:
            bad, reason = payload_looks_like_error(data, lat, lon)
            if bad:
                print(f"  → Find My error: {reason}")
                mac_alert(
                    "Find My error",
                    f"Can't get coords for Daniel.\n{reason}",
                )
                ntfy("Find My error", reason)

        if "daniel" in name and lat is not None and lon is not None:
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return
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
                    mac_alert("Left home", f"Moved {dist:.0f} m · {lat:.5f}, {lon:.5f}")

                if dist >= 5 and not (prev_home and currently_home):
                    msg = f"{dist:.0f} m ({dist*3.28:.0f} ft)\n{lat:.5f},{lon:.5f}"
                    ntfy("Daniel moved", msg)
                    mac_alert(
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
print(f"Mac alert: {ALERT_BIN} ({'ready' if ALERT_BIN.exists() else 'MISSING'})")
print(f"Find My silence alarm after {FIND_MY_SILENCE_SEC}s with no posts")
print("Listening on http://127.0.0.1:8080")
threading.Thread(target=silence_watch_loop, daemon=True).start()
HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
