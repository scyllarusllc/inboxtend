"""FastAPI entry point for the existing Inbox Tend HTTP contract.

The legacy route implementation is kept behind this adapter while endpoints are
ported to native FastAPI routers. This preserves sessions and CSRF behavior.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from io import BytesIO

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app import Handler, ROOT
from auth import AuthManager, read_config
from tasks import DEFAULT_DB
import gmail_web
import translation_preload


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
    gmail_web.start_auto_sync(DEFAULT_DB, config)
    translation_preload.start(DEFAULT_DB, config)
    yield


app = FastAPI(title='Inbox Tend', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


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
