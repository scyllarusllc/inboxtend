"""Prepare the project's Python environment before `make dev` starts the server."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / '.venv'
PYTHON = VENV / 'bin' / 'python'
REQUIREMENTS = ROOT / 'requirements.txt'
STAMP = VENV / '.inboxtend-requirements-sha256'


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def install_venv_support() -> None:
    if not shutil.which('apt-get'):
        raise RuntimeError('Python venv/ensurepip is missing; install the venv package for this Python version')
    command = [] if os.geteuid() == 0 else ['sudo']
    if command and not shutil.which('sudo'):
        raise RuntimeError('Python venv/ensurepip is missing and sudo is unavailable')
    package = f'python{sys.version_info.major}.{sys.version_info.minor}-venv'
    print(f'Installing {package} for the project environment…', flush=True)
    run(*command, 'apt-get', 'update')
    run(*command, 'apt-get', 'install', '-y', package)


def modules_ready() -> bool:
    if not PYTHON.is_file():
        return False
    result = subprocess.run([str(PYTHON), '-c',
                             'import cryptography, langdetect, ctranslate2; from argostranslate import package, translate'],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def main() -> int:
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if STAMP.is_file() and STAMP.read_text().strip() == digest and modules_ready():
        print('Python dependencies are ready.', flush=True)
        return 0
    if not PYTHON.is_file() or subprocess.run([str(PYTHON), '-m', 'pip', '--version'],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        print('Preparing .venv…', flush=True)
        result = subprocess.run([sys.executable, '-m', 'venv', str(VENV)], cwd=ROOT,
                                capture_output=True, text=True)
        if result.returncode:
            if 'ensurepip' not in result.stderr + result.stdout:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Could not create .venv')
            install_venv_support()
            run(sys.executable, '-m', 'venv', str(VENV))
    print('Installing Python dependencies from requirements.txt…', flush=True)
    run(str(PYTHON), '-m', 'pip', 'install', '-r', str(REQUIREMENTS))
    if not modules_ready():
        raise RuntimeError('Dependencies were installed, but the required modules cannot be imported')
    STAMP.write_text(digest + '\n')
    print('Python dependencies are ready.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Cannot prepare Inbox Tend: {exc}', file=sys.stderr)
        raise SystemExit(1) from None
