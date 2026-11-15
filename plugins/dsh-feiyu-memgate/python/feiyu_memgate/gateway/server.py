"""The HTTP face of the gateway: POST /v1/recall, POST /v1/remember, POST /v1/used, GET /healthz.

Standard library only. The server is built to stay answerable: a connection cap, a
wall-clock deadline for reading a request, bounded bodies, and an access log that
holds a method, a route, a status, a client name and a duration, never content.
Every error body is {"error": <human text>, "code": <stable code>}.
"""
import json
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024
MAX_CONNECTIONS = 32
READ_DEADLINE = 10.0
ROUTES = {("GET", "/healthz"): None, ("POST", "/v1/recall"): "recall", ("POST", "/v1/remember"): "remember",
          ("POST", "/v1/used"): "used"}
ROUTE_PATHS = {path for _, path in ROUTES}
BUSY_BODY = json.dumps({"error": "busy", "code": "busy"}).encode("utf-8")


def _error(text, code):
    return {"error": text, "code": code}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "gateway"
    sys_version = ""
    timeout = 10

    # http.server's default logging includes the request line; write nothing.
    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self._linger = False
        self.server.watch(self.request)

    def finish(self):
        self.server.unwatch(self.request)
        super().finish()
        if self._linger:
            self._swallow()

    def _swallow(self):
        """Close our side, then read briefly what the client may still send.

        A client may send its body without waiting for 100 Continue. Closing with that data
        unread would reset the connection and the client could lose the answer.
        """
        end = time.monotonic() + 0.5  # bounded in time too: a dribbling client cannot pin the thread
        try:
            self.request.shutdown(socket.SHUT_WR)
            self.request.settimeout(0.05)
            left = MAX_BODY
            while left > 0 and time.monotonic() < end:
                chunk = self.request.recv(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass

    def _expects_continue(self):
        """True when the client sent Expect: 100-continue (HTTP/1.1) and still waits for our
        go-ahead before it sends the body."""
        return (self.request_version == "HTTP/1.1" and not self._continued
                and self.headers.get("Expect", "").strip().lower() == "100-continue")

    def _send_continue(self):
        # An interim answer: the client sends the body now instead of after its own timeout
        # (curl waits a whole second for it).
        if self._expects_continue():
            self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
            self.wfile.flush()
            self._continued = True

    def _drain_body(self):
        """Read and discard an unread request body, so the reply survives the close.

        Closing a socket with unread data resets the connection, and the client
        can lose the answer. At most MAX_BODY bytes are read, and nothing without a
        valid Content-Length. The bytes are never kept, parsed or logged. The read
        deadline still applies: the watchdog ends a client that stops sending.
        A client waiting for 100 Continue has sent no body: it gets the answer at once.
        """
        self._body_read = True
        if self._expects_continue():
            self.close_connection = True
            self._linger = True
            return
        try:
            remaining = min(int(self.headers.get("Content-Length", "")), MAX_BODY)
        except ValueError:
            return
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass

    def _reply(self, status, obj):
        if not self._body_read:
            self._drain_body()
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":  # a HEAD answer is the status and headers only
            self.wfile.write(data)
        self._status = status

    # One do_<METHOD> per method, each passing its own name: a method/path pair that is not in the
    # route table is a 405, and the access log shows the real method.
    for _method in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        locals()["do_" + _method] = (lambda name: lambda self: self._handle(name))(_method)
    del _method

    def _handle(self, method):
        gw = self.server.gw
        started = time.monotonic()
        self._status = 0
        self._note = ""
        self._body_read = False
        self._continued = False
        client = None
        path = self.path.split("?", 1)[0]
        log_path = path if path in ROUTE_PATHS else "-"
        try:
            client = self._route(gw, method, path)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            if not self._status:
                try:
                    self._reply(500, _error("internal error", "internal"))
                except Exception:
                    pass
        finally:
            self.server.unwatch(self.request)
            gw.log(method, log_path, self._status, client, started, self._note)

    def _route(self, gw, method, path):
        """Answer the request. Returns the client name once it is known (for the log)."""
        if path not in ROUTE_PATHS:
            self._reply(404, _error("not found", "not_found"))
            return None
        if (method, path) not in ROUTES:
            self._reply(405, _error("method not allowed", "method_not_allowed"))
            return None
        if path == "/healthz":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            self._reply(200, gw.health(deep=query.get("deep") == ["1"]))
            return None
        header = self.headers.get("Authorization") or ""
        scheme, _, presented = header.partition(" ")
        caller = gw.identify(presented.strip()) if scheme.lower() == "bearer" else None
        if caller is None:
            self._reply(401, _error("unauthorized", "unauthorized"))
            return None
        # bound_tenant comes from the token alone (None unless it is a minted tenant token); it is
        # handed to the gateway beside the body, never written into it.
        name, bound_tenant = caller
        action = ROUTES[(method, path)]
        refused = gw.admit(name, action, bound_tenant)
        if refused is not None:
            self._note = refused.note
            self._reply(refused.status, refused.body)
            return name
        body = self._read_json()
        if body is None:
            return name
        reply = {"recall": gw.recall, "remember": gw.remember, "used": gw.used}[action](name, body, bound_tenant)
        self._note = reply.note
        self._reply(reply.status, reply.body)
        return name

    def _read_json(self):
        """Parse the body into a dict; replies and returns None on any problem."""
        length = self.headers.get("Content-Length")
        if length is None:
            self._reply(411, _error("length required", "length_required"))
            return None
        try:
            n = int(length)
        except ValueError:
            n = -1
        if n < 0:
            self._reply(400, _error("bad request", "bad_request"))
            return None
        if n > MAX_BODY:
            self.close_connection = True
            if self._expects_continue():
                self._linger = True
            else:
                # Drain a bounded amount so the client can read the answer.
                remaining = min(n, 1024 * 1024)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            self._body_read = True
            self._reply(413, _error("body too large", "too_large"))
            return None
        self._body_read = True
        self._send_continue()
        raw = self.rfile.read(n)
        # Request fully read: from here on the backend deadlines apply.
        self.server.unwatch(self.request)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            body = None
        if not isinstance(body, dict):
            self._reply(400, _error("bad request", "bad_request"))
            return None
        return body


class Server(ThreadingHTTPServer):
    """Threaded server with a connection cap and a wall-clock read deadline.

    Over the cap, new connections get an immediate 503 (code busy) and are closed; this
    also applies to /healthz, which is therefore not answered while the cap is
    reached. A single watchdog thread shuts down sockets still reading their
    request after `read_deadline` seconds, so byte-dribbling clients cannot pin
    a thread.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, max_connections=MAX_CONNECTIONS, read_deadline=READ_DEADLINE):
        super().__init__(addr, handler)
        self.max_connections = max_connections
        self.read_deadline = read_deadline
        self._active = 0
        self._lock = threading.Lock()
        self._watched = {}
        self._halt = threading.Event()
        self._watchdog = threading.Thread(target=self._watch_loop, daemon=True)
        self._watchdog.start()

    @property
    def active(self):
        with self._lock:
            return self._active

    def watch(self, sock):
        with self._lock:
            self._watched[sock] = time.monotonic() + self.read_deadline

    def unwatch(self, sock):
        with self._lock:
            self._watched.pop(sock, None)

    def _watch_loop(self):
        while not self._halt.wait(min(0.2, max(self.read_deadline / 4, 0.02))):
            now = time.monotonic()
            with self._lock:
                expired = [s for s, t in self._watched.items() if t <= now]
                for s in expired:
                    del self._watched[s]
            for s in expired:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def process_request(self, request, client_address):
        with self._lock:
            full = self._active >= self.max_connections
            if not full:
                self._active += 1
        if full:
            try:
                request.settimeout(1)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json; charset=utf-8\r\n"
                                b"Content-Length: %d\r\n\r\n%s" % (len(BUSY_BODY), BUSY_BODY))
                # Swallow what the client already sent: closing with unread
                # data would reset the connection and lose the 503.
                request.shutdown(socket.SHUT_WR)
                request.settimeout(0.05)
                for _ in range(4):
                    if not request.recv(65536):
                        break
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release()

    def handle_error(self, request, client_address):
        # Resets and watchdog disconnects land here; the default prints a traceback.
        pass

    def _release(self):
        with self._lock:
            self._active -= 1

    def server_close(self):
        self._halt.set()
        super().server_close()
        self._watchdog.join(2)
        gw = getattr(self, "gw", None)
        if gw is not None:
            gw.close()


def make_server(gw, host, port, **kwargs):
    server = Server((host, port), Handler, **kwargs)
    server.gw = gw
    return server


def serve(gw, host="127.0.0.1", port=8080, **kwargs):
    """Run the gateway until interrupted."""
    server = make_server(gw, host, port, **kwargs)
    try:
        gw.start()  # inside the try: a piece that fails to start still gets the others closed
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
