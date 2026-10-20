#!/usr/bin/env python3
"""
clip_processor.py — segment master.jsonl into clips.

Motion (clips.json):
  - Ignore points with accuracy > 30 m
  - Sustained movement ≥ 10 m before starting a clip
  - End after 40 s of sub-threshold stillness
  - Filters out couch↔kitchen / bed fidgets inside the home noise floor

All (all_points.json):
  - Same accuracy filter
  - One clip per consecutive hop that moved ≥ 0.5 m
  - Matches the catcher's terminal "moved X m" deltas (not one giant day clip)
"""
import json
from pathlib import Path
from datetime import datetime
from math import radians, sin, cos, sqrt, atan2

LOG = Path.home() / "track" / "master.jsonl"
OUT = Path.home() / "track" / "clips.json"
OUT_ALL = Path.home() / "track" / "all_points.json"

HOME_LAT = 34.102029
HOME_LON = -118.346478
HOME_RADIUS_M = 30.0

ACC_MAX = 30.0

# Motion (filtered) thresholds
MIN_MOVE_M = 10.0
STATIONARY_SEC = 40.0

# All (unfiltered micro-hops) — anything the terminal would call a real move
ALL_MIN_MOVE_M = 0.5


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
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
                        "home": o.get("home", is_home(float(o["lat"]), float(o["lon"]))),
                        "stale": bool(o.get("stale", False)),
                    })
            except Exception:
                continue
    points.sort(key=lambda p: p["ts"])
    return points


def clip_from_path(path, mode="motion"):
    dist_total = sum(
        haversine_m(path[m]["lat"], path[m]["lon"], path[m + 1]["lat"], path[m + 1]["lon"])
        for m in range(len(path) - 1)
    )
    duration = (path[-1]["ts"] - path[0]["ts"]).total_seconds()
    avg_speed = (dist_total / duration) if duration > 0 else 0
    max_speed = 0.0
    for m in range(len(path) - 1):
        d = haversine_m(path[m]["lat"], path[m]["lon"], path[m + 1]["lat"], path[m + 1]["lon"])
        t = (path[m + 1]["ts"] - path[m]["ts"]).total_seconds()
        if t > 0:
            max_speed = max(max_speed, d / t)
    home_related = any(p["home"] for p in path) or is_home(path[0]["lat"], path[0]["lon"])
    return {
        "start": path[0]["ts"].isoformat(),
        "end": path[-1]["ts"].isoformat(),
        "duration_sec": round(duration, 1),
        "distance_m": round(dist_total, 1),
        "avg_mph": round(avg_speed * 2.23694, 1),
        "max_mph": round(max_speed * 2.23694, 1),
        "home_related": home_related,
        "mode": mode,
        "points": [
            [p["lat"], p["lon"], p["ts"].isoformat(), p.get("acc")]
            for p in path
        ],
    }


def heartbeat_clip(points, mode):
    if not points:
        return None
    last = points[-1]
    return {
        "start": last["ts"].isoformat(),
        "end": last["ts"].isoformat(),
        "duration_sec": 0,
        "distance_m": 0,
        "avg_mph": 0,
        "max_mph": 0,
        "home_related": last["home"],
        "mode": mode,
        "points": [[last["lat"], last["lon"], last["ts"].isoformat(), last.get("acc")]],
    }


def build_motion_clips(points):
    """Filtered clips: sustained ≥10 m moves (hides indoor fidgets)."""
    if len(points) < 2:
        return []
    clips = []
    i = 0
    n = len(points)
    while i < n - 1:
        start_idx = None
        for j in range(i, n - 1):
            dist = haversine_m(
                points[j]["lat"], points[j]["lon"],
                points[j + 1]["lat"], points[j + 1]["lon"],
            )
            if dist >= MIN_MOVE_M:
                if j + 2 < n:
                    dist2 = haversine_m(
                        points[j + 1]["lat"], points[j + 1]["lon"],
                        points[j + 2]["lat"], points[j + 2]["lon"],
                    )
                    if dist2 >= MIN_MOVE_M * 0.6 or dist >= MIN_MOVE_M * 1.5:
                        start_idx = j
                        break
                else:
                    start_idx = j
                    break
        if start_idx is None:
            break

        end_idx = start_idx
        last_move_time = points[start_idx]["ts"]
        for k in range(start_idx + 1, n):
            dist = haversine_m(
                points[k - 1]["lat"], points[k - 1]["lon"],
                points[k]["lat"], points[k]["lon"],
            )
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

        path = points[start_idx : end_idx + 1]
        if len(path) >= 2:
            clips.append(clip_from_path(path, mode="motion"))
        i = end_idx + 1

    hb = heartbeat_clip(points, "motion")
    if hb and (not clips or clips[-1]["end"] < hb["end"]):
        clips.append(hb)
    return clips


def build_all_clips(points):
    """
    Unfiltered micro-clips: every consecutive hop ≥ ALL_MIN_MOVE_M.
    Same deltas the catcher prints as 'moved X m' — not one clip per day.
    """
    if len(points) < 2:
        return []
    clips = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        dist = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        if dist < ALL_MIN_MOVE_M:
            continue
        clips.append(clip_from_path([a, b], mode="all"))

    hb = heartbeat_clip(points, "all")
    if hb and (not clips or clips[-1]["end"] < hb["end"]):
        clips.append(hb)
    return clips


def main():
    points = load_points()
    print(f"Loaded {len(points)} usable points")

    clips = build_motion_clips(points)
    print(f"Generated {len(clips)} motion clips (≥{MIN_MOVE_M:g} m sustained)")
    with OUT.open("w") as f:
        json.dump(clips, f, indent=2)
    print(f"Wrote {OUT}")

    all_clips = build_all_clips(points)
    print(f"Generated {len(all_clips)} all-motion hops (≥{ALL_MIN_MOVE_M:g} m)")
    with OUT_ALL.open("w") as f:
        json.dump(all_clips, f)
    print(f"Wrote {OUT_ALL}")


if __name__ == "__main__":
    main()
