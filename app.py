"""Inbox Tend local-only demo. No real email account or AI service is connected."""
from __future__ import annotations
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
MESSAGES = [
 {"id":"sample-1","from":"alex@example.org","subject":"Website login issue","date":"2026-11-15","body":"Hi, the login form sometimes fails after submitting. Could you investigate and let us know what you find?","priority":"high"},
 {"id":"sample-2","from":"team@example.org","subject":"Friday project update","date":"2026-11-14","body":"Please send a short progress update for the dashboard project when you have time.","priority":"medium"},
 {"id":"sample-3","from":"newsletter@example.org","subject":"Weekly design newsletter","date":"2026-11-13","body":"This week's design roundup includes ideas for accessible forms and simpler navigation.","priority":"low"}
]
def summarize(message):
    """A deterministic excerpt, not an AI summary."""
    s = " ".join(message["body"].split())
    return s if len(s) <= 150 else s[:147].rstrip() + "..."

def draft_reply(message):
    """An unsent template; never falsely reports work as completed."""
    return ("Hello,\n\nThank you for your email regarding '" + message["subject"] + "'. "
            "I have received your request and will review the details before confirming next steps.\n\nBest regards")

class Handler(BaseHTTPRequestHandler):
    def _send(self,status,payload,mime):
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(payload)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)
    def _json(self,status,data):
        self._send(status,json.dumps(data).encode(),"application/json; charset=utf-8")
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/":
            self._send(200,(ROOT/"static"/"index.html").read_bytes(),"text/html; charset=utf-8")
        elif path=="/static/app.js":
            self._send(200,(ROOT/"static"/"app.js").read_bytes(),"text/javascript; charset=utf-8")
        elif path=="/api/messages":
            self._json(200,{"mode":"demo","messages":[dict(m,summary=summarize(m)) for m in MESSAGES]})
        elif path=="/api/health":
            self._json(200,{"status":"ok","mode":"demo"})
        else:
            self._json(404,{"error":"Not found"})
    def do_POST(self):
        if urlparse(self.path).path!="/api/draft":
            return self._json(404,{"error":"Not found"})
        if self.headers.get("Content-Type","").split(";")[0].strip().lower()!="application/json":
            return self._json(415,{"error":"Content-Type must be application/json"})
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length<1 or length>4096: raise ValueError("Invalid length")
            data=json.loads(self.rfile.read(length))
        except (ValueError,TypeError):
            return self._json(400,{"error":"Invalid JSON request"})
        msg=next((m for m in MESSAGES if m["id"]==data.get("id")),None) if isinstance(data,dict) else None
        if msg is None: return self._json(404,{"error":"Message not found"})
        self._json(200,{"draft":draft_reply(msg),"sent":False,"mode":"demo"})
    def log_message(self,*args):
        pass

if __name__=="__main__":
    host,port="127.0.0.1",8765
    print(f"Inbox Tend demo: http://{host}:{port}")
    print("Fictional sample messages only. No account connected. No email sending.")
    ThreadingHTTPServer((host,port),Handler).serve_forever()
