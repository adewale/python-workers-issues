"""Local HTTP echo server for tests whose Worker makes outbound requests.

The server replies like httpbin.org/headers (``{"headers": {...}}`` with
Title-Cased names) and also records every request it receives. Tests assert on
that record, so the oracle is what actually arrived on the wire, not what the
Worker reports about itself.
"""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def _title_case(name):
    return "-".join(part.capitalize() for part in name.split("-"))


class _EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        headers = {_title_case(k): v for k, v in self.headers.items()}
        query = parse_qs(urlsplit(self.path).query)
        self.server.received.append({"query": query, "headers": headers})

        body = json.dumps({"headers": headers}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def echo_server():
    """Yield ``(url, received)`` for a server on an ephemeral localhost port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/headers", server.received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
