"""Export one task and its locally cached sender correspondence to a private folder."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from tasks import connection


def root_dir():
    return Path(os.environ.get('INBOX_TEND_TASKS_DIR', '/tasks')).resolve()


def project_path(task_id: int):
    return root_dir() / str(task_id)


def _safe_name(name: str):
    clean = re.sub(r'[^\w. -]', '_', name, flags=re.UNICODE).strip(' .')[:120]
    return clean or 'attachment'


def export_task(db_path, user_id: int, task_id: int):
    with connection(db_path) as db:
        task = db.execute('SELECT * FROM tasks WHERE id=? AND user_id=?', (task_id, user_id)).fetchone()
        if task is None: raise LookupError('Task not found')
        task_data = dict(task)
        source = db.execute('SELECT peer_email FROM gmail_messages WHERE user_id=? AND message_id=?',
                            (user_id, task['message_id'])).fetchone()
        address = source['peer_email'] if source else ''
        messages = [dict(row) for row in db.execute('SELECT message_id,account_id,from_addr,to_addr,subject,date_header,internal_date,body_text,snippet '
                    'FROM gmail_messages WHERE user_id=? AND peer_email=? ORDER BY internal_date,message_id',
                    (user_id, address))] if address else []
        root = root_dir()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        destination = root / str(task_id)
        if destination.exists():
            if not destination.is_dir() or destination.is_symlink(): raise OSError('Invalid project directory')
        existing = destination.exists()
        working = Path(tempfile.mkdtemp(prefix=f'.task-{task_id}-', dir=root))
        try:
            files = working / 'attachments'
            files.mkdir(mode=0o700)
            (working / 'task.json').write_text(json.dumps(task_data, ensure_ascii=False, indent=2), encoding='utf-8')
            (working / 'emails.json').write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding='utf-8')
            manifest = []
            used = set()
            with zipfile.ZipFile(working / 'attachments.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for index, mail in enumerate(messages, 1):
                    for row in db.execute('SELECT filename,mime_type,size_bytes,content,skipped_reason FROM gmail_attachments '
                                          'WHERE user_id=? AND message_id=? ORDER BY id', (user_id, mail['message_id'])):
                        name = f'{index:04d}-{_safe_name(row["filename"])}'
                        while name in used: name = f'{index:04d}-copy-{name}'
                        used.add(name)
                        item = {'message_id': mail['message_id'], 'name': row['filename'], 'file': name if row['content'] is not None else None,
                                'size_bytes': row['size_bytes'], 'skipped_reason': row['skipped_reason'] if row['content'] is None else ''}
                        manifest.append(item)
                        if row['content'] is not None:
                            content = bytes(row['content'])
                            (files / name).write_bytes(content)
                            archive.write(files / name, arcname=name)
                # Include task copies when their source attachment is no longer in the mail cache.
                exported_source_names = {(item['message_id'], item['name']) for item in manifest}
                for row in db.execute('SELECT filename,mime_type,size_bytes,content,skipped_reason FROM task_attachments WHERE task_id=? ORDER BY id', (task_id,)):
                    if (task['message_id'], row['filename']) in exported_source_names:
                        continue
                    name = f'task-{_safe_name(row["filename"])}'
                    while name in used: name = 'copy-' + name
                    used.add(name)
                    manifest.append({'message_id': task['message_id'], 'name': row['filename'], 'file': name if row['content'] is not None else None,
                                     'size_bytes': row['size_bytes'], 'skipped_reason': row['skipped_reason'] if row['content'] is None else ''})
                    if row['content'] is not None:
                        (files / name).write_bytes(bytes(row['content']))
                        archive.write(files / name, arcname=name)
            (working / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            os.chmod(working, 0o700)
            for path in working.rglob('*'):
                if path.is_file(): os.chmod(path, 0o600)
            backup = None
            if existing:
                backup = Path(tempfile.mkdtemp(prefix=f'.old-task-{task_id}-', dir=root))
                backup.rmdir()
                destination.rename(backup)
            try: working.rename(destination)
            except Exception:
                if backup is not None: backup.rename(destination)
                raise
            if backup is not None: shutil.rmtree(backup)
            return {'path': str(destination), 'existing': existing, 'emails': len(messages),
                    'attachments': sum(item['file'] is not None for item in manifest),
                    'unavailable': sum(item['file'] is None for item in manifest)}
        except Exception:
            shutil.rmtree(working, ignore_errors=True)
            raise
