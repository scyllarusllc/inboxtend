"""FastAPI entry point for the existing Inbox Tend HTTP contract.

The legacy route implementation is kept behind this adapter while endpoints are
ported to native FastAPI routers. This preserves sessions and CSRF behavior.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from io import BytesIO
import os
import re
import secrets
import sqlite3
import traceback

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import Response, HTMLResponse
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app import Handler, ROOT, PAGES
from auth import AuthManager, read_config
from tasks import DEFAULT_DB
import gmail_web
import translation_preload
from task_api import router as task_router
from task_api import current_user
from mail_design import preview_document
from mail_content import looks_like_html
from tasks import connection


class RequestAdapter(Handler):
    def __init__(self, request: Request, body: bytes):
        self.path = str(request.url.path) + (('?' + request.url.query) if request.url.query else '')
        self.command = request.method
        self.headers = request.headers
        self.rfile = BytesIO(body)
        self.response: Response | None = None

    def _send(self, status, payload, mime, extra_headers=()):
        headers = {
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        }
        self.response = Response(payload, status_code=status, media_type=None, headers={**headers, 'Content-Type': mime})
        for key, value in extra_headers:
            if key.lower() == 'set-cookie':
                self.response.headers.append(key, value)
            else:
                self.response.headers[key] = value

    def _redirect(self, location, extra_headers=()):
        self.response = Response(status_code=303, headers={'Location': location, 'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer'})
        for key, value in extra_headers:
            self.response.headers.append(key, value)

    def _host_ok(self):
        # TrustedHost is enforced by the legacy route; Request.headers keeps its exact Host value.
        return super()._host_ok()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config = read_config(ROOT / '.env')
    RequestAdapter.gmail_config = config
    RequestAdapter.auth_manager = AuthManager(DEFAULT_DB,
        username=config.get('INBOX_TEND_ADMIN_USERNAME', ''),
        password_hash=config.get('INBOX_TEND_ADMIN_PASSWORD_HASH', ''))
    _app.state.auth_manager = RequestAdapter.auth_manager
    _app.state.db_path = DEFAULT_DB
    _app.state.gmail_config = config
    gmail_web.start_auto_sync(DEFAULT_DB, config)
    translation_preload.start(DEFAULT_DB, config)
    yield


app = FastAPI(title='Inbox Tend', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(task_router)
_dev_boot = secrets.token_hex(8)


@app.get('/api/connectors/gmail/messages/{message_id}/design')
def email_design(request: Request, message_id: str, remote_images: bool = False, user=Depends(current_user)):
    path = getattr(request.app.state, 'db_path', DEFAULT_DB)
    with connection(path) as db:
        row = db.execute('SELECT body_html,body_text FROM gmail_messages WHERE user_id=? AND message_id=?',
                         (user['id'], message_id)).fetchone()
    if row is None or not (row['body_html'] or looks_like_html(row['body_text'])):
        raise HTTPException(404, 'HTML design unavailable')
    policy = "sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:" + (' https:' if remote_images else '') + "; font-src data:; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
    return HTMLResponse(preview_document(row['body_html'] or row['body_text'], remote_images), headers={
        'Content-Security-Policy': policy, 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})


@app.get('/api/dev/version')
def dev_version(page: str = ''):
    if os.environ.get('INBOX_TEND_DEV_RELOAD') != '1':
        return Response(status_code=404)
    page_name = 'mail' if re.fullmatch(r'/user/mail/[A-Za-z0-9._:~%-]+', page) else 'task' if re.fullmatch(r'/user/task/\d+', page) else {'/': 'home', '/login': 'login', '/privacy': 'privacy',
        '/user/inbox': 'inbox', '/user/tasks': 'tasks', '/user/settings': 'settings'}.get(page)
    if page_name is None:
        return Response(status_code=400)
    files = [ROOT / 'static' / 'layout.html', ROOT / 'static' / 'pages' / f'{page_name}.html']
    scripts = [ROOT / script.lstrip('/') for script in PAGES[page_name][2]]
    scripts.append(ROOT / 'static' / 'dev-reload.js')
    if PAGES[page_name][4]:
        scripts.append(ROOT / 'static' / 'stimulus-app.js')
    changed = lambda paths: max((path.stat().st_mtime_ns for path in paths if path.is_file()), default=0)
    return JSONResponse({'server': _dev_boot, 'markup': changed(files),
                         'scripts': changed(scripts),
                         'styles': changed([ROOT / 'static' / 'styles.css'])},
                        headers={'Cache-Control': 'no-store'})


@app.exception_handler(HTTPException)
async def api_error(_request: Request, exc: HTTPException):
    return JSONResponse({'error': exc.detail}, status_code=exc.status_code,
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@app.exception_handler(sqlite3.Error)
@app.exception_handler(SQLAlchemyError)
async def database_error(_request: Request, _exc):
    traceback.print_exc()
    return JSONResponse({'error': 'Task database unavailable'}, status_code=503,
                        headers={'Cache-Control': 'no-store'})


@app.api_route('/{path:path}', methods=['GET', 'POST', 'PATCH', 'DELETE'])
async def dispatch(request: Request, path: str):
    body = bytearray()
    if request.method in ('POST', 'PATCH', 'DELETE'):
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16_384:
                return Response(b'{"error":"Invalid JSON request"}', status_code=400,
                                media_type='application/json', headers={'Cache-Control': 'no-store'})
    handler = RequestAdapter(request, body)
    handler._handle(request.method in ('POST', 'PATCH', 'DELETE'))
    return handler.response or Response(status_code=500)
