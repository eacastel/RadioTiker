# tunnel_manager.py

import os
import time
import threading
import subprocess
from typing import Optional


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class TunnelConfig:
    def __init__(
        self,
        ssh_host: str,
        ssh_user: str,
        ssh_key_path: str,
        remote_port: int,
        local_port: int,
        ssh_port: int = 22,
    ):
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_key_path = ssh_key_path
        self.remote_port = int(remote_port)
        self.local_port = int(local_port)
        self.ssh_port = int(ssh_port)

    def public_base_url(self) -> str:
        return f"http://127.0.0.1:{self.remote_port}"

    def ssh_cmd(self) -> list[str]:
        return [
            "ssh",
            "-N",
            "-T",
            "-o", "BatchMode=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            "-i", self.ssh_key_path,
            "-p", str(self.ssh_port),
            "-R", f"{self.remote_port}:127.0.0.1:{self.local_port}",
            f"{self.ssh_user}@{self.ssh_host}",
        ]


class ReverseTunnel:
    def __init__(self, cfg: TunnelConfig, log_fn=print):
        self.cfg = cfg
        self.log = log_fn
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._last_err: Optional[str] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._terminate_proc()

    def _terminate_proc(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _run(self):
        backoff = 2
        if not os.path.exists(self.cfg.ssh_key_path):
            self.log(f"❌ SSH key not found: {self.cfg.ssh_key_path}")
            return

        while not self._stop.is_set():
            cmd = self.cfg.ssh_cmd()
            self.log(f"🔒 Tunnel: starting ssh reverse tunnel to {self.cfg.ssh_host}:{self.cfg.remote_port}")
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                while self._proc.poll() is None and not self._stop.is_set():
                    time.sleep(1)
                if self._proc and self._proc.stderr:
                    try:
                        err = (self._proc.stderr.read() or "").strip()
                    except Exception:
                        err = ""
                    if err:
                        # Keep log concise; first line is usually enough (auth/host key/port blocked).
                        first = err.splitlines()[0]
                        self._last_err = first
                        self.log(f"⚠️ Tunnel ssh error: {first}")
            except Exception as e:
                self.log(f"❌ Tunnel error: {e}")

            if self._stop.is_set():
                break

            self.log(f"⚠️ Tunnel stopped. Reconnecting in {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def start_tunnel_from_env(local_port: int, log_fn=print):
    if not _env_bool("TUNNEL_ENABLE", False):
        return None, None

    ssh_host = os.getenv("SSH_HOST", "").strip()
    ssh_user = os.getenv("SSH_USER", "").strip()
    ssh_key_path = os.getenv("SSH_KEY_PATH", "").strip()
    ssh_port = int(os.getenv("SSH_PORT", "22"))
    remote_port = os.getenv("REMOTE_PORT", "").strip()

    if not (ssh_host and ssh_user and ssh_key_path and remote_port):
        log_fn("❌ Tunnel config incomplete. Set SSH_HOST, SSH_USER, SSH_KEY_PATH, REMOTE_PORT.")
        return None, None

    cfg = TunnelConfig(
        ssh_host=ssh_host,
        ssh_user=ssh_user,
        ssh_key_path=ssh_key_path,
        remote_port=int(remote_port),
        local_port=int(local_port),
        ssh_port=ssh_port,
    )
    tunnel = ReverseTunnel(cfg, log_fn=log_fn)
    tunnel.start()
    return tunnel, cfg.public_base_url()
