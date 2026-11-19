"""Stop an older Inbox Tend dev process listening on the requested TCP port."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


PROJECT = Path(__file__).resolve().parents[1]


def listeners(port: int) -> set[int]:
    try:
        result = subprocess.run(['lsof', '-nP', f'-tiTCP:{port}', '-sTCP:LISTEN'],
                                capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise RuntimeError('lsof is required to check the dev port') from None
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or 'Could not inspect the dev port')
    return {int(line) for line in result.stdout.splitlines() if line.isdecimal()}


def inbox_dev_process(pid: int) -> bool:
    try:
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        cwd = Path(f'/proc/{pid}/cwd').resolve()
    except (OSError, RuntimeError):
        return False
    if not args or not Path(os.fsdecode(args[0])).name.startswith('python'):
        return False
    scripts = [Path(os.fsdecode(arg)) for arg in args[1:] if arg]
    if not scripts or scripts[0].name != 'app.py':
        return False
    app = (cwd / scripts[0]).resolve()
    return app == PROJECT / 'app.py' or app.is_relative_to('/srv/inboxtend/releases')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('port', type=int)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be 1–65535')
    try:
        occupied = listeners(args.port)
        unsafe = [pid for pid in occupied if not inbox_dev_process(pid)]
        if unsafe:
            raise RuntimeError(f'Port {args.port} belongs to another process (PID {", ".join(map(str, unsafe))}); it was not stopped')
        for pid in occupied:
            print(f'Stopping previous Inbox Tend process {pid} on port {args.port}…', flush=True)
            try: os.kill(pid, signal.SIGTERM)
            except ProcessLookupError: pass
        for _ in range(50):
            if not listeners(args.port): return 0
            time.sleep(.1)
        raise RuntimeError(f'Port {args.port} is still occupied after 5 seconds')
    except (OSError, RuntimeError) as exc:
        print(f'Cannot start Inbox Tend: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
