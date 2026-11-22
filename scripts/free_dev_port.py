"""Stop an older Inbox Tend API or Next.js dev process on the requested port."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
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
    found = {int(line) for line in result.stdout.splitlines() if line.isdecimal()}
    # lsof may omit Node's IPv6 listener on some systems; ss reports its PID.
    if not found:
        try:
            status = subprocess.run(['ss', '-H', '-ltnp'], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            status = None
        if status and status.returncode == 0:
            for line in status.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 4 and fields[3].rsplit(':', 1)[-1] == str(port):
                    found.update(int(pid) for pid in re.findall(r'pid=(\d+)', line))
    return found


def inbox_dev_process(pid: int) -> bool:
    try:
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        cwd = Path(f'/proc/{pid}/cwd').resolve()
    except (OSError, RuntimeError):
        return False
    if not args:
        return False
    program = Path(os.fsdecode(args[0])).name
    if program.startswith('next-server'):
        return cwd == PROJECT
    if not program.startswith('python'):
        return False
    # Uvicorn's --reload worker inherits the listening socket. Its command line
    # is a multiprocessing bootstrap, so verify the parent is this project's reloader.
    if len(args) > 2 and args[1] == b'-c' and b'from multiprocessing.spawn import spawn_main' in args[2]:
        try:
            status = Path(f'/proc/{pid}/status').read_text()
            parent = int(re.search(r'^PPid:\s*(\d+)$', status, re.MULTILINE).group(1))
            parent_args = Path(f'/proc/{parent}/cmdline').read_bytes().split(b'\0')
        except (OSError, AttributeError, ValueError):
            return False
        return cwd == PROJECT and b'--reload' in parent_args and inbox_dev_process(parent)
    scripts = [Path(os.fsdecode(arg)) for arg in args[1:] if arg]
    if not scripts or scripts[0].name not in ('app.py', 'cli.py'):
        return False
    app = (cwd / scripts[0]).resolve()
    if app != PROJECT / scripts[0].name and not app.is_relative_to('/srv/inboxtend/releases'):
        return False
    return scripts[0].name == 'app.py' or (len(args) > 2 and args[2] == b'serve')


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
