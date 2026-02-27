"""httpx drops the User-Agent header inside Cloudflare Python Workers.

pywrangler installs httpx from a Pyodide-specific fork (hoodmane/httpx)
that replaces httpcore with a jsfetch.py transport.  This transport
filters out User-Agent to avoid CORS preflight failures in browsers.
Workers are not browsers — there are no CORS restrictions on outbound
fetch — but the filter still applies:

    python_modules/httpx/_transports/jsfetch.py line 57:
        HEADERS_TO_IGNORE = ("user-agent",)

Hit /test to see the proof: httpbin.org echoes back the headers the
server actually received.  httpx drops User-Agent; js.fetch() preserves it.
"""

import json

from js import Object, fetch
from pyodide.ffi import to_js
from workers import Response, WorkerEntrypoint

ECHO_URL = "https://httpbin.org/headers"

HEADERS = {
    "User-Agent": "repro/1.0",
    "X-Custom": "preserved",
}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if "/test" in request.url:
            return await self._test()
        return Response(
            "GET /test — prove httpx drops User-Agent but keeps other headers\n",
            headers={"content-type": "text/plain"},
        )

    async def _test(self):
        import httpx

        # httpx path — uses jsfetch.py transport in Pyodide
        async with httpx.AsyncClient() as client:
            resp = await client.get(ECHO_URL, headers=HEADERS, timeout=10.0)
        httpx_received = resp.json().get("headers", {})

        # js.fetch() path — direct, no filtering
        opts = to_js(
            {"method": "GET", "headers": HEADERS},
            dict_converter=Object.fromEntries,
        )
        js_resp = await fetch(ECHO_URL, opts)
        jsfetch_received = json.loads(await js_resp.text()).get("headers", {})

        def pick(received):
            lower_keys = {k.lower() for k in HEADERS}
            return {k: v for k, v in received.items() if k.lower() in lower_keys}

        return Response.json({
            "headers_sent": HEADERS,
            "httpx_received": pick(httpx_received),
            "jsfetch_received": pick(jsfetch_received),
        })
