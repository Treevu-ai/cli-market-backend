#!/usr/bin/env python3
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "routers" / "health.py"
t = p.read_text(encoding="utf-8")

if "in_progress" not in t:
    t = t.replace(
        "    finished = last[\"finished_at\"]\n    if finished:",
        "    finished = last[\"finished_at\"]\n    in_progress = finished is None\n    if finished:",
        1,
    )
    t = t.replace(
        '        "status": status,\n        "last_run": last["started_at"],',
        '        "status": status,\n        "in_progress": in_progress,\n        "last_run": last["started_at"],',
        1,
    )
    t = t.replace(
        '        "stores_succeeded": last["stores_succeeded"],\n        "prices_collected": last["prices_collected"],',
        '        "stores_succeeded": last["stores_succeeded"] if not in_progress else None,\n'
        '        "prices_collected": last["prices_collected"] if not in_progress else None,',
        1,
    )
    p.write_text(t, encoding="utf-8")
    print("patched health.py")
else:
    print("health.py already patched")