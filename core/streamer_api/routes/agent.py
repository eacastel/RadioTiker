from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os
import time
import secrets
import string
import uuid
import subprocess
from typing import Dict, Any, Optional, Set

from ..storage import load_agent, save_agent_record, list_assigned_ports

router = APIRouter(prefix="/api/agent", tags=["agent"])

# In-memory, v1-only stores (replace with DB later)
DEVICE_LINKS: Dict[str, Dict[str, Any]] = {}
AGENT_TOKENS: Dict[str, Dict[str, Any]] = {}

DEVICE_CODE_TTL = 600  # 10 min
AGENT_TOKEN_TTL = 3600 * 24  # 24h

REMOTE_PORT_MIN = 44000
REMOTE_PORT_MAX = 44999

DEFAULT_SSH_HOST = os.getenv("SSH_TUNNEL_HOST", "tunnel.radio.tiker.es")
DEFAULT_SSH_USER = os.getenv("SSH_TUNNEL_USER", "rtunnel")
PROVISION_SCRIPT = os.getenv(
    "RTUNNEL_PROVISION_SCRIPT",
    "/usr/local/sbin/radiotiker-provision-rtunnel-key",
)
PROVISION_WITH_SUDO = os.getenv("RTUNNEL_PROVISION_USE_SUDO", "1").strip() in ("1", "true", "yes")


class LinkStartRequest(BaseModel):
    device_name: Optional[str] = None
    agent_version: Optional[str] = None


class LinkStartResponse(BaseModel):
    device_code: str
    link_url: str
    expires_in: int


class LinkCompleteRequest(BaseModel):
    device_code: str
    user_id: str


class LinkCompleteResponse(BaseModel):
    agent_token: str
    agent_id: str


class RegisterKeyRequest(BaseModel):
    agent_token: str
    public_key: str
    local_port: int


class RegisterKeyResponse(BaseModel):
    remote_port: int
    ssh_user: str
    ssh_host: str


class HeartbeatRequest(BaseModel):
    agent_token: str
    tunnel_ok: bool = True
    last_scan: Optional[int] = None


def _now() -> int:
    return int(time.time())


def _gen_device_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(2)
    )


def _purge_expired():
    now = _now()
    for code in list(DEVICE_LINKS.keys()):
        if DEVICE_LINKS[code]["expires_at"] <= now:
            DEVICE_LINKS.pop(code, None)
    for tok in list(AGENT_TOKENS.keys()):
        if AGENT_TOKENS[tok]["expires_at"] <= now:
            AGENT_TOKENS.pop(tok, None)


def _allocate_port() -> int:
    used: Set[int] = set(list_assigned_ports())
    for p in range(REMOTE_PORT_MIN, REMOTE_PORT_MAX + 1):
        if p not in used:
            return p
    raise HTTPException(status_code=503, detail="No remote ports available")


def _provision_rtunnel_key(user_id: str, agent_id: str, remote_port: int, public_key: str):
    if not PROVISION_SCRIPT:
        return
    cmd = [PROVISION_SCRIPT, user_id, str(remote_port), agent_id]
    if PROVISION_WITH_SUDO:
        cmd = ["sudo"] + cmd
    try:
        res = subprocess.run(
            cmd,
            input=public_key,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"key provisioning failed: {e}")

    if res.returncode != 0:
        err = (res.stderr or res.stdout or "unknown provisioning error").strip()
        raise HTTPException(status_code=500, detail=f"key provisioning failed: {err[:300]}")


@router.post("/link/start", response_model=LinkStartResponse)
def link_start(payload: LinkStartRequest):
    _purge_expired()
    code = _gen_device_code()
    DEVICE_LINKS[code] = {
        "device_name": payload.device_name,
        "agent_version": payload.agent_version,
        "created_at": _now(),
        "expires_at": _now() + DEVICE_CODE_TTL,
    }
    link_url = f"https://next.radio.tiker.es/link/{code}"
    return LinkStartResponse(
        device_code=code,
        link_url=link_url,
        expires_in=DEVICE_CODE_TTL,
    )


@router.post("/link/complete", response_model=LinkCompleteResponse)
def link_complete(payload: LinkCompleteRequest):
    _purge_expired()
    rec = DEVICE_LINKS.get(payload.device_code)
    if not rec:
        raise HTTPException(status_code=400, detail="Invalid or expired device_code")

    agent_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(24)
    AGENT_TOKENS[token] = {
        "user_id": payload.user_id,
        "agent_id": agent_id,
        "created_at": _now(),
        "expires_at": _now() + AGENT_TOKEN_TTL,
    }
    DEVICE_LINKS.pop(payload.device_code, None)
    return LinkCompleteResponse(agent_token=token, agent_id=agent_id)


@router.post("/register-key", response_model=RegisterKeyResponse)
def register_key(payload: RegisterKeyRequest):
    _purge_expired()
    rec = AGENT_TOKENS.get(payload.agent_token)
    if not rec:
        raise HTTPException(status_code=401, detail="Invalid or expired agent_token")

    remote_port = _allocate_port()
    user_id = rec["user_id"]
    agent_id = rec["agent_id"]
    ssh_host = DEFAULT_SSH_HOST
    ssh_user = DEFAULT_SSH_USER

    _provision_rtunnel_key(
        user_id=user_id,
        agent_id=agent_id,
        remote_port=remote_port,
        public_key=payload.public_key,
    )

    st = load_agent(user_id)
    st.update(
        {
            "agent_id": agent_id,
            "public_key": payload.public_key,
            "ssh_host": ssh_host,
            "ssh_user": ssh_user,
            "remote_port": remote_port,
            "local_port": payload.local_port,
            "base_url": f"http://127.0.0.1:{remote_port}",
            "last_seen": _now(),
        }
    )
    save_agent_record(user_id, st)

    return RegisterKeyResponse(
        remote_port=remote_port,
        ssh_user=ssh_user,
        ssh_host=ssh_host,
    )


@router.post("/heartbeat")
def heartbeat(payload: HeartbeatRequest):
    _purge_expired()
    rec = AGENT_TOKENS.get(payload.agent_token)
    if not rec:
        raise HTTPException(status_code=401, detail="Invalid or expired agent_token")

    user_id = rec["user_id"]
    st = load_agent(user_id)
    st["last_seen"] = _now()
    st["tunnel_ok"] = bool(payload.tunnel_ok)
    if payload.last_scan is not None:
        st["last_scan"] = int(payload.last_scan)
    save_agent_record(user_id, st)
    return {"ok": True}
