# local_file_server.py
import os, threading, socket, subprocess, re, shutil
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import quote

class _QuietHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # helps some clients

    def log_message(self, format, *args):
        pass

    def do_HEAD(self):
        try:
            f = self.send_head()
            if f:
                f.close()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self):
        try:
            f = self.send_head()
            if not f:
                return
            try:
                self.copyfile(f, self.wfile)
            finally:
                f.close()
        except (BrokenPipeError, ConnectionResetError):
            return

    def copyfile(self, source, outputfile):
        try:
            shutil.copyfileobj(source, outputfile, length=64 * 1024)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def finish(self):
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()

        ctype = self.guess_type(path)
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        fs = os.fstat(f.fileno())
        size = fs.st_size

        # Always advertise range support
        range_header = self.headers.get("Range")
        if not range_header:
            self.send_response(200)
            self.send_header("Content-type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return f

        # Parse: Range: bytes=start-end
        m = re.match(r"bytes=(\d+)-(\d*)$", range_header.strip())
        if not m:
            f.close()
            self.send_error(416, "Invalid Range")
            return None

        start = int(m.group(1))
        end_s = m.group(2)
        end = int(end_s) if end_s else (size - 1)

        if start >= size or start < 0 or end < start:
            f.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return None

        if end >= size:
            end = size - 1

        length = (end - start) + 1
        f.seek(start)

        self.send_response(206)
        self.send_header("Content-type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return f


class _RootedThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def _lan_ip_fallback() -> str:
    ip = "127.0.0.1"
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        pass
    finally:
        try:
            if s:
                s.close()
        except Exception:
            pass
    return ip

def _tailscale_ip() -> str | None:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, check=True, timeout=2
        ).stdout
        for line in out.strip().splitlines():
            ip = line.strip()
            if ip.startswith("100."):
                return ip
    except Exception:
        pass
    return None

def _tailscale_iface_ip() -> str | None:
    try:
        import fcntl, struct  # Linux only
        iface = "tailscale0"
        if not os.path.exists(f"/sys/class/net/{iface}"):
            return None
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            return socket.inet_ntoa(
                fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', iface[:15].encode()))[20:24]
            )
        finally:
            s.close()
    except Exception:
        return None

class LocalFileServer:
    """
    Serves files under root_dir over HTTP.
    Base URL priority:
      1) PUBLIC_BASE_URL env (full http(s)://host:port)
      2) Tailscale IP (100.x.x.x)
      3) LAN IP
    """
    def __init__(self, root_dir: str, port: int = 8765, public_base_url: str | None = None):
        self.root_dir = os.path.abspath(root_dir)
        self.port = port
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self._httpd = None
        self._thread = None
        self._chosen_ip = None

    def _choose_ip(self) -> str:
        if self.public_base_url:
            return self.public_base_url  # full base URL already
        if not self._chosen_ip:
            self._chosen_ip = _tailscale_ip() or _tailscale_iface_ip() or _lan_ip_fallback()
        return f"http://{self._chosen_ip}:{self.port}"

    def base_url(self) -> str:
        return self._choose_ip()

    def path_to_url(self, abs_path: str) -> str:
        rel = os.path.relpath(os.path.abspath(abs_path), self.root_dir).replace(os.sep, "/")
        encoded = "/".join(quote(p) for p in rel.split("/"))
        return f"{self._choose_ip()}/{encoded}"

    def start(self):
        if not os.path.isdir(self.root_dir):
            raise RuntimeError(f"LocalFileServer root does not exist: {self.root_dir}")
        handler = lambda *a, **k: _QuietHandler(*a, directory=self.root_dir, **k)
        self._httpd = _RootedThreadingHTTPServer(("0.0.0.0", self.port), handler)
        def run():
            try:
                self._httpd.serve_forever(poll_interval=0.5)
            except Exception:
                pass
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        try:
            if self._httpd:
                self._httpd.shutdown()
                self._httpd.server_close()
        except Exception:
            pass
        finally:
            self._httpd = None
            self._thread = None
