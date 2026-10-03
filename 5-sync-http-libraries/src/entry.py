"""Verify synchronous HTTP libraries work in Cloudflare Python Workers.

Older guidance said sync HTTP clients such as requests/urllib3 would fail in
Python Workers with "blocking call in async context". This Worker calls both
libraries directly from an async fetch handler and returns what httpbin echoed
back. If either library still hits the old failure mode, /test returns 500.

The test suite passes ``?echo=http://127.0.0.1:<port>/headers`` so the requests
go to a local echo server it controls and can inspect; only loopback URLs are
accepted. Without the parameter the Worker calls httpbin.org.
"""

from urllib.parse import parse_qs, urlsplit

from workers import Response, WorkerEntrypoint

ECHO_URL = "https://httpbin.org/headers"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")

HEADERS = {
    "User-Agent": "sync-repro/1.0",
    "X-Custom": "preserved",
}


def _echo_url(request_url):
    """Return the echo URL: httpbin.org, or a loopback ``?echo=`` override.

    The override is rebuilt from its parsed host, port and path, so a URL that
    urllib.parse and requests/urllib3 would read differently (userinfo,
    backslashes) cannot reach another host.
    """
    override = parse_qs(urlsplit(request_url).query).get("echo")
    if not override:
        return ECHO_URL
    raw = override[0]
    parts = urlsplit(raw)
    if (
        parts.scheme != "http"
        or parts.hostname not in LOOPBACK_HOSTS
        or "@" in parts.netloc
        or "\\" in raw
        or parts.query
        or parts.fragment
    ):
        raise ValueError("echo URL must be http://127.0.0.1:<port>/<path>")
    port = f":{parts.port}" if parts.port else ""
    return f"http://{parts.hostname}{port}{parts.path}"


def _pick_sent_headers(received):
    lower_keys = {k.lower() for k in HEADERS}
    return {k: v for k, v in received.items() if k.lower() in lower_keys}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if "/test" in request.url:
            try:
                echo_url = _echo_url(request.url)
            except ValueError as error:
                return Response(f"{error}\n", status=400)
            return self._test(echo_url)
        return Response(
            "GET /test — verify requests and urllib3 work in Python Workers\n",
            headers={"content-type": "text/plain"},
        )

    def _test(self, echo_url):
        import requests
        import urllib3

        results = {}

        requests_resp = requests.get(
            f"{echo_url}?client=requests", headers=HEADERS, timeout=10
        )
        requests_resp.raise_for_status()
        results["requests"] = {
            "status_code": requests_resp.status_code,
            "received": _pick_sent_headers(requests_resp.json().get("headers", {})),
        }

        http = urllib3.PoolManager()
        urllib3_resp = http.request(
            "GET",
            f"{echo_url}?client=urllib3",
            headers=HEADERS,
            timeout=urllib3.Timeout(connect=10.0, read=10.0),
        )
        if urllib3_resp.status >= 400:
            raise RuntimeError(f"urllib3 returned HTTP {urllib3_resp.status}")
        results["urllib3"] = {
            "status_code": urllib3_resp.status,
            "received": _pick_sent_headers(urllib3_resp.json().get("headers", {})),
        }

        return Response.json({"headers_sent": HEADERS, "results": results})
