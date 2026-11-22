"""Native FastAPI read routes for the task workspace."""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func
from sqlmodel import select

from auth import cookie_token
from db_models import Task, TaskAttachment, TaskImageReview, session as model_session
from tasks import DEFAULT_DB, connection, decorate, _soon, events, task_work_plan
from app import raster_preview_mime


router = APIRouter(prefix='/api/tasks')


def current_user(request: Request):
    host = request.headers.get('host', '')
    name = host.split(':', 1)[0].lower()
    if name not in ('127.0.0.1', 'localhost', '[::1]') or host.count(':') > 1:
        raise HTTPException(403, 'Invalid Host')
    manager = request.app.state.auth_manager
    user = manager.session(cookie_token(request.headers.get('cookie'))) if manager else None
    if not user:
        raise HTTPException(401, 'Sign in required')
    return user


def json_result(data):
    return JSONResponse(data, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})


def task_rows(user_id, path=DEFAULT_DB):
    with connection(path) as db:
        soon = _soon(db, user_id)
    with model_session(path) as db:
        rows = db.exec(select(Task, func.count(TaskAttachment.id))
            .outerjoin(TaskAttachment, TaskAttachment.task_id == Task.id)
            .where(Task.user_id == user_id).group_by(Task.id)
            .order_by(Task.status == 'done', Task.due_date.is_(None), Task.due_date, Task.due_time, Task.id)).all()
    return [decorate(dict(task.model_dump(), image_count=count), due_soon_hours=soon) for task, count in rows]


@router.get('/summary')
def summary(request: Request, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    rows = task_rows(user['id'], path)
    by_status = {name: sum(task['status'] == name for task in rows)
                 for name in ('new', 'in_progress', 'waiting', 'done')}
    due_soon = sum(task['deadline_state'] == 'due_soon' for task in rows)
    overdue = sum(task['deadline_state'] == 'overdue' for task in rows)
    with connection(path) as db:
        from settings import effective
        zone = effective(db, user['id'])['timezone']
    return json_result({'summary': {'total': len(rows), 'by_status': by_status,
                                    'due_soon': due_soon, 'overdue': overdue}, 'timezone': zone})


@router.get('')
def list_saved_tasks(request: Request, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with connection(path) as db:
        from settings import effective
        zone = effective(db, user['id'])['timezone']
    return json_result({'tasks': task_rows(user['id'], path), 'timezone': zone})


@router.get('/{task_id:int}')
def task_detail(request: Request, task_id: int, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        task = db.exec(select(Task).where(Task.id == task_id, Task.user_id == user['id'])).first()
        if task is None: raise HTTPException(404, 'Task not found')
        count = db.exec(select(func.count(TaskAttachment.id)).where(TaskAttachment.task_id == task_id)).one()
    with connection(path) as db: soon = _soon(db, user['id'])
    return json_result({'task': decorate(dict(task.model_dump(), image_count=count), due_soon_hours=soon)})


@router.get('/{task_id:int}/events')
def task_events(request: Request, task_id: int, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        exists = db.exec(select(Task.id).where(Task.id == task_id, Task.user_id == user['id'])).first()
    if exists is None: raise HTTPException(404, 'Task not found')
    with connection(path) as db: history = events(db, task_id, user['id'])
    return json_result({'events': history})


@router.get('/{task_id:int}/plan')
def saved_plan(request: Request, task_id: int, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        exists = db.exec(select(Task.id).where(Task.id == task_id, Task.user_id == user['id'])).first()
    if exists is None: raise HTTPException(404, 'Task not found')
    with connection(path) as db: saved = task_work_plan(db, task_id, user['id'])
    return json_result({'saved': saved})


@router.get('/{task_id:int}/images')
def images(request: Request, task_id: int, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        exists = db.exec(select(Task.id).where(Task.id == task_id, Task.user_id == user['id'])).first()
        if exists is None: raise HTTPException(404, 'Task not found')
        files = db.exec(select(TaskAttachment).where(TaskAttachment.task_id == task_id)
                        .order_by(TaskAttachment.id)).all()
        review = db.get(TaskImageReview, task_id)
        saved = dict(json.loads(review.result_json), created_at=review.created_at) if review and review.user_id == user['id'] else None
        metadata = [{'id': file.id, 'name': file.filename, 'mime_type': file.mime_type,
                     'size_bytes': file.size_bytes, 'stored': file.content is not None,
                     'skipped_reason': file.skipped_reason} for file in files]
    return json_result({'images': metadata, 'review': saved})


@router.get('/{task_id:int}/images/{image_id:int}/{action}')
def image_file(request: Request, task_id: int, image_id: int, action: str, user=Depends(current_user)):
    if action not in ('thumbnail', 'download'): raise HTTPException(404, 'Not found')
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with model_session(path) as db:
        row = db.exec(select(TaskAttachment).join(Task, Task.id == TaskAttachment.task_id)
            .where(Task.id == task_id, Task.user_id == user['id'], TaskAttachment.id == image_id)).first()
        if row is None: raise HTTPException(404, 'Task image not found')
        if row.content is None: raise HTTPException(404, 'Task image was not stored')
        content, filename, mime_type = row.content, row.filename, row.mime_type
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
               'Referrer-Policy': 'no-referrer',
               'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}
    if action == 'thumbnail':
        mime = raster_preview_mime(mime_type, content)
        if not mime: raise HTTPException(415, 'Image preview unavailable')
        headers['Content-Disposition'] = 'inline'
        return Response(content, media_type=mime, headers=headers)
    safe = ''.join(c if c.isascii() and (c.isalnum() or c in '._-') else '_' for c in filename)[:100] or 'image'
    headers['Content-Disposition'] = f'attachment; filename="{safe}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(content, media_type='application/octet-stream', headers=headers)
