#!/usr/bin/env python3
"""
regenerate.py — refresh clips.json and optionally update viewer.

Run after new data arrives (or via cron).
"""
import subprocess
import sys
from pathlib import Path

TRACK = Path.home() / "track"
PROCESSOR = TRACK / "clip_processor.py"

def main():
    if not PROCESSOR.exists():
        print("clip_processor.py not found")
        sys.exit(1)
    print("Running clip processor...")
    subprocess.check_call([sys.executable, str(PROCESSOR)])
    print("Done. clips.json updated.")
    print("Restart or refresh the viewer to pick up new clips.")

if __name__ == "__main__":
    main()
