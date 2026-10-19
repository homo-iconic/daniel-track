#!/usr/bin/env python3
"""
clip_processor.py — segment master.jsonl into motion clips.
Lowest-practical thresholds:
- Ignore points with accuracy > 30 m
- Require sustained movement: ≥ 10 m over at least 2 consecutive intervals
  (or a short rolling window) before starting a clip.
- End clip after 40 s of sub-threshold movement.
- Tag home-related clips.
Output: clips.json (list of clip objects)
"""
import json
from pathlib import Path
from datetime import datetime
from math import radians, sin, cos, sqrt, atan2

LOG = Path.home() / "track" / "master.jsonl"
OUT = Path.home() / "track" / "clips.json"

HOME_LAT = 34.102029
HOME_LON = -118.346478
HOME_RADIUS_M = 30.0

# Thresholds (lowest practical)
ACC_MAX = 30.0          # ignore worse accuracy
MIN_MOVE_M = 10.0       # minimum distance to count as real movement
MIN_SAMPLES = 2         # consecutive samples needed
STATIONARY_SEC = 40.0   # end clip after this much stillness

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

def is_home(lat, lon):
    return haversine_m(lat, lon, HOME_LAT, HOME_LON) <= HOME_RADIUS_M

def load_points():
    points = []
    if not LOG.exists():
        return points
    with LOG.open() as f:
        for line in f:
            try:
                o = json.loads(line)
                if o.get("name") and "daniel" in o["name"].lower() and o.get("lat") is not None:
                    acc = o.get("accuracy")
                    if acc is not None and float(acc) > ACC_MAX:
                        continue
                    points.append({
                        "ts": datetime.fromisoformat(o["ts"]),
                        "lat": float(o["lat"]),
                        "lon": float(o["lon"]),
                        "acc": float(acc) if acc is not None else None,
                        "home": o.get("home", is_home(float(o["lat"]), float(o["lon"])))
                    })
            except Exception:
                continue
    points.sort(key=lambda p: p["ts"])
    return points

def build_clips(points):
    if len(points) < 2:
        return []
    clips = []
    i = 0
    n = len(points)
    while i < n - 1:
        # Look for start of movement
        start_idx = None
        for j in range(i, n - 1):
            dist = haversine_m(points[j]["lat"], points[j]["lon"],
                               points[j+1]["lat"], points[j+1]["lon"])
            if dist >= MIN_MOVE_M:
                # Check sustained (next sample also moves or is close)
                if j + 2 < n:
                    dist2 = haversine_m(points[j+1]["lat"], points[j+1]["lon"],
                                        points[j+2]["lat"], points[j+2]["lon"])
                    if dist2 >= MIN_MOVE_M * 0.6 or dist >= MIN_MOVE_M * 1.5:
                        start_idx = j
                        break
                else:
                    start_idx = j
                    break
        if start_idx is None:
            break

        # Grow the clip until stationary for STATIONARY_SEC
        end_idx = start_idx
        last_move_time = points[start_idx]["ts"]
        for k in range(start_idx + 1, n):
            dist = haversine_m(points[k-1]["lat"], points[k-1]["lon"],
                               points[k]["lat"], points[k]["lon"])
            if dist >= MIN_MOVE_M * 0.5:
                last_move_time = points[k]["ts"]
                end_idx = k
            else:
                still = (points[k]["ts"] - last_move_time).total_seconds()
                if still >= STATIONARY_SEC:
                    end_idx = k - 1
                    break
                end_idx = k
        if end_idx < start_idx:
            end_idx = start_idx

        # Build clip
        path = points[start_idx:end_idx+1]
        if len(path) < 2:
            i = end_idx + 1
            continue
        dist_total = sum(haversine_m(path[m]["lat"], path[m]["lon"],
                                      path[m+1]["lat"], path[m+1]["lon"])
                         for m in range(len(path)-1))
        duration = (path[-1]["ts"] - path[0]["ts"]).total_seconds()
        avg_speed = (dist_total / duration) if duration > 0 else 0
        max_speed = 0
        for m in range(len(path)-1):
            d = haversine_m(path[m]["lat"], path[m]["lon"], path[m+1]["lat"], path[m+1]["lon"])
            t = (path[m+1]["ts"] - path[m]["ts"]).total_seconds()
            if t > 0:
                max_speed = max(max_speed, d / t)
        home_related = any(p["home"] for p in path) or is_home(path[0]["lat"], path[0]["lon"])
        clips.append({
            "start": path[0]["ts"].isoformat(),
            "end": path[-1]["ts"].isoformat(),
            "duration_sec": round(duration, 1),
            "distance_m": round(dist_total, 1),
            "avg_mph": round(avg_speed * 2.23694, 1),
            "max_mph": round(max_speed * 2.23694, 1),
            "home_related": home_related,
            "points": [[p["lat"], p["lon"], p["ts"].isoformat()] for p in path]
        })
        i = end_idx + 1

    # Always include the most recent point as a heartbeat clip if it is newer
    if points:
        last = points[-1]
        if not clips or clips[-1]["end"] < last["ts"].isoformat():
            clips.append({
                "start": last["ts"].isoformat(),
                "end": last["ts"].isoformat(),
                "duration_sec": 0,
                "distance_m": 0,
                "avg_mph": 0,
                "max_mph": 0,
                "home_related": last["home"],
                "points": [[last["lat"], last["lon"], last["ts"].isoformat()]]
            })
    return clips

def main():
    points = load_points()
    print(f"Loaded {len(points)} usable points")
    clips = build_clips(points)
    print(f"Generated {len(clips)} clips")
    with OUT.open("w") as f:
        json.dump(clips, f, indent=2)
    print(f"Wrote {OUT}")

    # Also write every usable point as a single “all” clip for the SHOW ALL toggle
    if points:
        all_clip = {
            "start": points[0]["ts"].isoformat(),
            "end": points[-1]["ts"].isoformat(),
            "duration_sec": (points[-1]["ts"] - points[0]["ts"]).total_seconds(),
            "distance_m": 0,
            "avg_mph": 0,
            "max_mph": 0,
            "home_related": False,
            "points": [[p["lat"], p["lon"], p["ts"].isoformat()] for p in points]
        }
        with (Path.home() / "track" / "all_points.json").open("w") as f:
            json.dump([all_clip], f)
        print("Wrote all_points.json")

if __name__ == "__main__":
    main()
