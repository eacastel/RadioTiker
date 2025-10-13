# local_file_server.py
import os, threading, socket, subprocess
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer
from urllib.parse import quote

class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # quieter logs
        pass

class _RootedTCPServer(TCPServer):
    allow_reuse_address = True

def _lan_ip_fallback() -> str:
    ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        pass
    finally:
        try: s.close()
        except Exception: pass
    return ip

def _tailscale_ip() -> str | None:
    # Works if tailscale is installed + logged in. Returns first IPv4 (100.x.y.z)
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, check=True, timeout=2
        ).stdout.strip().splitlines()
        for line in out:
            ip = line.strip()
            if ip.startswith("100."):
                return ip
    except Exception:
        pass
    return None

def _tailscale_iface_ip() -> str | None:
    # Fallback if the CLI isn't available but the iface exists.
    try:
        import fcntl, struct
        iface = "tailscale0"
        if not os.path.exists(f"/sys/class/net/{iface}"):
            return None
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(fcntl.ioctl(
            s.fileno(), 0x8915, struct.pack('256s', iface[:15].encode('utf-8'))
        )[20:24])
    except Exception:
        return None

class LocalFileServer:
    """
    Serves files under root_dir over HTTP.
    Base URL priority:
      1) PUBLIC_BASE_URL env (full http(s)://host:port)
      2) Tailscale IP (100.x.x.x)
      3) LAN IP fallback
    """
    def __init__(self, root_dir: str, port: int = 8765, public_base_url: str | None = None):
        self.root_dir = os.path.abspath(root_dir)
        self.port = port
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self._httpd = None
        self._thread = None
        self._chosen_ip = None  # memoize

    def _base_host(self) -> str:
        if self.public_base_url:
            return self.public_base_url  # already full URL like http://host:port
        if not self._chosen_ip:
            self._chosen_ip = _tailscale_ip() or _tailscale_iface_ip() or _lan_ip_fallback()
        return f"http://{self._chosen_ip}:{self.port}"

    def base_url(self) -> str:
        return self._base_host()

    def path_to_url(self, abs_path: str) -> str:
        rel = os.path.relpath(os.path.abspath(abs_path), self.root_dir).replace(os.sep, "/")
        encoded = "/".join(quote(p) for p in rel.split("/"))
        return f"{self._base_host()}/{encoded}"

    def start(self):
        if not os.path.isdir(self.root_dir):
            raise RuntimeError(f"LocalFileServer root does not exist: {self.root_dir}")
        handler_cls = lambda *args, **kw: _QuietHandler(*args, directory=self.root_dir, **kw)
        self._httpd = _RootedTCPServer(("0.0.0.0", self.port), handler_cls)
        def run():
            try: self._httpd.serve_forever()
            except Exception: pass
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        try:
            if self._httpd:
                self._httpd.shutdown()
                self._httpd.server_close()
        finally:
            self._httpd = None
            self._thread = None
