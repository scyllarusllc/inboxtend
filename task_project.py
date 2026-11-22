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
    return Path(os.environ.get('INBOX_TEND_TASKS_DIR', str(Path(__file__).resolve().parent / '.tasks'))).resolve()


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
                        item = {'message_id': mail['message_id'], 'name': row['filename'], 'mime_type': row['mime_type'], 'file': name if row['content'] is not None else None,
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
                    manifest.append({'message_id': task['message_id'], 'name': row['filename'], 'mime_type': row['mime_type'], 'file': name if row['content'] is not None else None,
                                     'size_bytes': row['size_bytes'], 'skipped_reason': row['skipped_reason'] if row['content'] is None else ''})
                    if row['content'] is not None:
                        (files / name).write_bytes(bytes(row['content']))
                        archive.write(files / name, arcname=name)
            (working / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            if existing:
                managed = {'task.json', 'emails.json', 'manifest.json', 'attachments.zip'}
                try:
                    old_manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
                    managed.update('attachments/' + item['file'] for item in old_manifest if item.get('file'))
                except (OSError, ValueError, TypeError):
                    pass
                for old in destination.rglob('*'):
                    if old.is_symlink() or not old.is_file():
                        continue
                    relative = old.relative_to(destination)
                    if relative.as_posix() in managed:
                        continue
                    target = working / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copy2(old, target)
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


PROJECT_TEXT_LIMIT = 30_000
PROJECT_FILE_LIMIT = 5_000
PROJECT_IMAGE_LIMIT = 8
PROJECT_IMAGE_BYTES = 2 * 1024 * 1024


def read_project(task_id: int):
    """Read a bounded snapshot; model receives content, never filesystem access."""
    folder = project_path(task_id)
    if not folder.is_dir() or folder.is_symlink():
        raise FileNotFoundError('Task project does not exist')
    parts = []
    remaining = PROJECT_TEXT_LIMIT
    image_files = []
    skipped = []
    text_suffixes = {'.txt', '.md', '.csv', '.json', '.log'}
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        name = path.relative_to(folder).as_posix()
        if name == 'attachments.zip':
            continue
        if len(parts) + len(image_files) + len(skipped) >= 250:
            skipped.append('More files omitted (250 file limit)')
            break
        size = path.stat().st_size
        suffix = path.suffix.lower()
        if suffix in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
            if len(image_files) >= PROJECT_IMAGE_LIMIT or size > PROJECT_IMAGE_BYTES:
                skipped.append(f'{name}: image limit or size exceeded')
                continue
            content = path.read_bytes()
            valid = {
                '.jpg': content.startswith(b'\xff\xd8\xff'), '.jpeg': content.startswith(b'\xff\xd8\xff'),
                '.png': content.startswith(b'\x89PNG\r\n\x1a\n'), '.gif': content.startswith((b'GIF87a', b'GIF89a')),
                '.webp': content.startswith(b'RIFF') and content[8:12] == b'WEBP',
            }
            if not valid[suffix]:
                skipped.append(f'{name}: invalid image data')
                continue
            mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}[suffix]
            image_files.append((name, mime, content))
        elif suffix in text_suffixes and remaining > 0:
            if size > 2_000_000:
                skipped.append(f'{name}: too large to read')
                continue
            content = path.read_bytes()
            if b'\x00' in content[:4096]:
                skipped.append(f'{name}: binary data')
                continue
            if name == 'emails.json':
                try:
                    emails = json.loads(content)
                    lines = [f'{len(emails)} saved emails, newest first:']
                    for mail in reversed(emails[-30:]):
                        lines.append(f"{mail.get('date_header', '')} | {mail.get('from_addr', '')} → {mail.get('to_addr', '')} | {mail.get('subject', '')}\n{(mail.get('body_text') or mail.get('snippet') or '')[:400]}")
                    source_text = '\n'.join(lines)
                except (ValueError, TypeError, AttributeError):
                    source_text = content.decode('utf-8', 'replace')
            else:
                source_text = content.decode('utf-8', 'replace')
            text = source_text[:min(PROJECT_FILE_LIMIT, remaining)]
            parts.append(f'File: {name}\n{text}')
            remaining -= len(text)
            if len(source_text) > len(text):
                skipped.append(f'{name}: text truncated')
        else:
            skipped.append(f'{name}: unsupported file type')
    text_files = len(parts)
    if skipped and remaining > 0:
        parts.append('Files not fully read:\n' + '\n'.join(skipped[:60]))
    return {'text': '\n\n'.join(parts), 'images': image_files,
            'text_files': text_files, 'image_count': len(image_files), 'skipped': len(skipped)}
