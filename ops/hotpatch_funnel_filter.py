#!/usr/bin/env python3
"""Push funnel filter files to the live Fly.io machine via SSH (chunked base64)."""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHUNK = 3000
FILES = (
    ROOT / "market_funnel.py",
    ROOT / "routers" / "funnel.py",
)


def _ssh(python_snippet: str) -> subprocess.CompletedProcess[str]:
    escaped = python_snippet.replace("\\", "\\\\").replace('"', '\\"')
    cmd = f"fly ssh console -a cli-market-api -C 'python -c \"{escaped}\"'"
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, shell=True)


def _upload(path: Path) -> None:
    rel = path.relative_to(ROOT).as_posix()
    target = f"/app/{rel}"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    init = f"open('{target}','wb').close()"
    proc = _ssh(init)
    if proc.returncode != 0:
        print(proc.stdout, proc.stderr, file=sys.stderr)
        raise RuntimeError(f"init failed for {rel}")

    for i in range(0, len(payload), CHUNK):
        chunk = payload[i : i + CHUNK]
        snippet = f"import base64; open('{target}','ab').write(base64.b64decode('{chunk}'))"
        proc = _ssh(snippet)
        if proc.returncode != 0:
            print(proc.stdout, proc.stderr, file=sys.stderr)
            raise RuntimeError(f"chunk failed for {rel} at {i}")
    print(f"wrote {target} ({path.stat().st_size} bytes)")


def _restart_uvicorn() -> None:
    restart_py = """
import os, signal, time, subprocess
uvicorn_pids = []
for name in os.listdir('/proc'):
    if not name.isdigit():
        continue
    try:
        cmd = open(f'/proc/{name}/cmdline', 'rb').read().decode('latin-1').replace(chr(0), ' ')
    except OSError:
        continue
    if 'uvicorn' in cmd and 'market_server' in cmd:
        uvicorn_pids.append(int(name))
for pid in uvicorn_pids:
    os.kill(pid, signal.SIGTERM)
    print('stopped', pid)
time.sleep(1)
port = os.environ.get('PORT', '8765')
subprocess.Popen(
    ['python', '-m', 'uvicorn', 'market_server:app', '--host', '0.0.0.0', '--port', port],
    cwd='/app',
    stdout=open('/tmp/uvicorn-reload.log', 'ab'),
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
print('started uvicorn on', port)
""".strip()
    proc = _ssh(restart_py)
    print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError("uvicorn restart failed")


def main() -> int:
    missing = [p for p in FILES if not p.exists()]
    if missing:
        print("Missing:", ", ".join(str(p) for p in missing), file=sys.stderr)
        return 1

    for path in FILES:
        _upload(path)
    _restart_uvicorn()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())