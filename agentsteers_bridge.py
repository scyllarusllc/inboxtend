"""Opt-in Inbox Tend -> AgentSteers mock MCP bridge.

A reviewed Inbox Tend task may be submitted ONLY after an authenticated user
explicitly confirms the dispatch. No Gmail token, raw message, attachments,
credentials or mailbox headers are transmitted. This adapter never executes
Codex, shell commands, or Gmail write actions.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MAX_RESPONSE = 65536

class BridgeError(Exception):
    pass


def configured():
    return bool(os.environ.get("INBOX_TEND_AGENTSTEERS_MCP_URL") and
                os.environ.get("INBOX_TEND_AGENTSTEERS_MCP_TOKEN") and
                os.environ.get("INBOX_TEND_AGENTSTEERS_REPOSITORY"))


def _settings():
    if os.environ.get("INBOX_TEND_AGENTSTEERS_BRIDGE_ENABLED") != "1":
        raise BridgeError("AgentSteers bridge is disabled")
    raw_url = os.environ.get("INBOX_TEND_AGENTSTEERS_MCP_URL", "")
    token = os.environ.get("INBOX_TEND_AGENTSTEERS_MCP_TOKEN", "")
    repo = os.environ.get("INBOX_TEND_AGENTSTEERS_REPOSITORY", "")
    p = urlsplit(raw_url)
    if (p.scheme != "https" or not p.hostname or p.username or p.password or
        p.query or p.fragment or p.path != "/mcp" or not p.netloc or
        p.hostname in ("localhost", "127.0.0.1", "::1") or
        not _REPO.fullmatch(repo or "") or len(token) < 32 or
        any(c in token for c in "\r\n")):
        raise BridgeError("AgentSteers bridge configuration is invalid")
    return raw_url, token, repo


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeError("AgentSteers redirects are not allowed")


def _call(name, arguments):
    url, token, _ = _settings()
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}}).encode()
    req = urllib.request.Request(url, payload, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req, timeout=8) as response:
            if response.status != 200:
                raise BridgeError("AgentSteers rejected the request")
            raw = response.read(_MAX_RESPONSE + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeError("AgentSteers connection failed") from exc
    if len(raw) > _MAX_RESPONSE:
        raise BridgeError("AgentSteers response too large")
    try:
        answer = json.loads(raw)
        if answer.get("jsonrpc") != "2.0" or answer.get("id") != 1:
            raise ValueError("Bad JSON-RPC response")
        result = answer["result"]
        if not isinstance(result, dict) or not isinstance(result["content"], list):
            raise ValueError("Bad MCP response")
        outcome = json.loads(result["content"][0]["text"])
        if not isinstance(outcome, dict):
            raise ValueError("Bad task result")
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise BridgeError("Invalid AgentSteers response") from exc
    if result.get("isError") or not outcome.get("ok"):
        raise BridgeError("AgentSteers rejected task: " + str(outcome.get("error", "unknown"))[:100])
    return outcome


def submit(task):
    _, _, repo = _settings()
    title = (task.get("title") or "").strip()
    description = (task.get("description") or "").strip()
    instruction = ("Inbox Tend reviewed task: " + title + "\n\n" + description)[:2000]
    return _call("submit_task", {"repository": repo, "instruction": instruction})


def status(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise BridgeError("Invalid AgentSteers task identifier")
    status_value = _call("get_task_status", {"task_id": job_id})
    result = _call("get_task_result", {"task_id": job_id})
    return {"task_id": job_id, "status": status_value.get("status"),
            "result": result.get("result", {})}
