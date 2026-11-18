#!/usr/bin/env python3
"""Consistent, compressed backup of the Inbox Tend SQLite database.

Uses SQLite's online backup API, so it is safe while the server is running (a plain file copy
of a WAL database is not). The copy is integrity-checked, gzip-compressed, stored with mode 600
in a 700 directory, and only the newest --keep backups are retained.

The backup contains your mail in plain form. It does NOT contain `.env`: the Gmail refresh token
is encrypted with INBOX_TEND_TOKEN_KEY, so keep a separate, private copy of `.env` as well.
"""
import argparse
import gzip
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def backup(db_path: Path, dest: Path, keep: int) -> Path:
    if not db_path.is_file():
        raise SystemExit(f'Database not found: {db_path}')
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest.chmod(0o700)
    now = datetime.now()
    stamp = now.strftime('%Y%m%d-%H%M%S-') + f'{now.microsecond:06d}'  # Sorts chronologically; collisions are not realistic.
    plain = dest / f'.tmp-{stamp}.sqlite3'
    final = dest / f'tasks-{stamp}.sqlite3.gz'
    created = False
    try:
        source = sqlite3.connect(db_path, timeout=30)
        target = sqlite3.connect(plain)
        try:
            source.backup(target)
            result = target.execute('PRAGMA integrity_check').fetchone()[0]
        finally:
            target.close()
            source.close()
        if result != 'ok':
            raise SystemExit(f'Integrity check failed for the backup: {result}')
        fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, 'wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as packed, open(plain, 'rb') as src:
            shutil.copyfileobj(src, packed)
    except BaseException:
        if created:  # Remove only the partial file this run created, never an earlier backup.
            final.unlink(missing_ok=True)
        raise
    finally:
        plain.unlink(missing_ok=True)
    if keep > 0:
        for old in sorted(dest.glob('tasks-*.sqlite3.gz'))[:-keep]:
            old.unlink()
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'tasks.sqlite3')
    parser.add_argument('--dest', type=Path, default=Path(os.environ.get('INBOX_TEND_BACKUP_DIR', '/var/backups/inboxtend')))
    parser.add_argument('--keep', type=int, default=int(os.environ.get('INBOX_TEND_BACKUP_KEEP', '14')),
                        help='number of backups to keep (default 14)')
    args = parser.parse_args()
    path = backup(args.db, args.dest, args.keep)
    count = len(list(args.dest.glob('tasks-*.sqlite3.gz')))
    print(f'Backup written: {path} ({path.stat().st_size} bytes); {count} kept in {args.dest}')


if __name__ == '__main__':
    sys.exit(main())
