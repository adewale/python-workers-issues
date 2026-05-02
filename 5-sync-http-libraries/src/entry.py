"""Verify synchronous HTTP libraries work in Cloudflare Python Workers.

Older guidance said sync HTTP clients such as requests/urllib3 would fail in
Python Workers with "blocking call in async context". This Worker calls both
libraries directly from an async fetch handler and returns what httpbin echoed
back. If either library still hits the old failure mode, /test returns 500.
"""

from workers import Response, WorkerEntrypoint

ECHO_URL = "https://httpbin.org/headers"

HEADERS = {
    "User-Agent": "sync-repro/1.0",
    "X-Custom": "preserved",
}


def _pick_sent_headers(received):
    lower_keys = {k.lower() for k in HEADERS}
    return {k: v for k, v in received.items() if k.lower() in lower_keys}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if "/test" in request.url:
            return self._test()
        return Response(
            "GET /test — verify requests and urllib3 work in Python Workers\n",
            headers={"content-type": "text/plain"},
        )

    def _test(self):
        import requests
        import urllib3

        results = {}

        requests_resp = requests.get(ECHO_URL, headers=HEADERS, timeout=10)
        requests_resp.raise_for_status()
        results["requests"] = {
            "status_code": requests_resp.status_code,
            "received": _pick_sent_headers(requests_resp.json().get("headers", {})),
        }

        http = urllib3.PoolManager()
        urllib3_resp = http.request(
            "GET",
            ECHO_URL,
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
