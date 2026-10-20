#!/usr/bin/env python3
"""Ping the local catcher; Mac-alert if it's down. Stopped by ./stop."""
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

TRACK = Path.home() / "track"
ALERT_BIN = TRACK / "motion_alert"
ALERT_PID = TRACK / "motion_alert.pid"
WATCH_PID = TRACK / "watchdog.pid"
PAUSE = TRACK / "watchdog.pause"  # present while ./stop is intentional
URL = "http://127.0.0.1:8080/"
INTERVAL = 20
FAILS_NEEDED = 2


def alert_up():
    if not ALERT_PID.exists():
        return False
    try:
        os.kill(int(ALERT_PID.read_text().strip()), 0)
        return True
    except Exception:
        return False


def mac_alert(title, message):
    if not ALERT_BIN.exists() or alert_up() or PAUSE.exists():
        return
    try:
        subprocess.Popen(
            [str(ALERT_BIN), title, message],
            cwd=str(TRACK),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"{time.strftime('%H:%M:%S')} → mac alert: {title}", flush=True)
    except Exception as e:
        print(f"alert failed: {e}", flush=True)


def main():
    WATCH_PID.write_text(str(os.getpid()))
    fails = 0
    print(f"Watchdog PID {os.getpid()} → {URL}", flush=True)
    while True:
        if PAUSE.exists():
            fails = 0
            time.sleep(INTERVAL)
            continue
        try:
            with urllib.request.urlopen(URL, timeout=3) as r:
                r.read(64)
            if fails:
                print(f"{time.strftime('%H:%M:%S')} catcher OK again", flush=True)
            fails = 0
        except Exception as e:
            fails += 1
            print(f"{time.strftime('%H:%M:%S')} catcher miss {fails}/{FAILS_NEEDED}: {e}", flush=True)
            if fails >= FAILS_NEEDED:
                mac_alert(
                    "Webhook down",
                    "Daddy Tracker catcher is not responding on :8080.\nRun: cd ~/track && start",
                )
                fails = 0  # wait for dismiss + next failures before re-nag
        time.sleep(INTERVAL)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: exit(0))
    signal.signal(signal.SIGINT, lambda *_: exit(0))
    main()
