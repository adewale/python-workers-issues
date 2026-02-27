# httpx Drops the `User-Agent` Header in Cloudflare Python Workers

## The Bug

`pywrangler` installs httpx from a [Pyodide-specific fork](https://github.com/hoodmane/httpx) that replaces httpcore with a `jsfetch.py` transport. This transport filters out the `User-Agent` header to prevent CORS preflight failures in browser-based Pyodide (see [pyodide-http#22](https://github.com/koenvo/pyodide-http/issues/22)):

```python
# python_modules/httpx/_transports/jsfetch.py line 57
HEADERS_TO_IGNORE = ("user-agent",)

# line 157 (_do_fetch)
headers = {k: v for k, v in request.headers.items() if k not in HEADERS_TO_IGNORE}
```

Cloudflare Workers are not browsers — there are no CORS restrictions on outbound fetch. But the filter still applies because the fork's emscripten support PR ([encode/httpx#3330](https://github.com/encode/httpx/pull/3330)) was closed without merge, so the browser-oriented workaround lives on.

Other headers (`Authorization`, `Accept`, custom headers) are transmitted normally. Only `User-Agent` is stripped. This causes 403s from APIs that require it — notably GitHub's API (`api.github.com/user`):

```
Request forbidden by administrative rules.
Please make sure your request has a User-Agent header.
```

## Reproduction

```bash
cd 3-httpx-headers
uv sync
uv run pywrangler dev
# Visit http://localhost:8787/test
```

The `/test` endpoint sends `{"User-Agent": "repro/1.0", "X-Custom": "preserved"}` to httpbin.org/headers (which echoes back received headers) via both httpx and `js.fetch()`.

### Actual output

```json
{
  "headers_sent": {
    "User-Agent": "repro/1.0",
    "X-Custom": "preserved"
  },
  "httpx_received": {
    "X-Custom": "preserved"
  },
  "jsfetch_received": {
    "User-Agent": "repro/1.0",
    "X-Custom": "preserved"
  }
}
```

`httpx_received` is missing `User-Agent`. `jsfetch_received` has both headers.

## Where the Fix Belongs

The `HEADERS_TO_IGNORE` filter exists in the [hoodmane/httpx](https://github.com/hoodmane/httpx) fork's `jsfetch.py` transport — originally from [pyodide-http](https://github.com/koenvo/pyodide-http/issues/22) to prevent CORS preflight failures in browsers. The upstream PR to merge emscripten support into encode/httpx ([#3330](https://github.com/encode/httpx/pull/3330)) was closed without merge, so the fork is the only path.

workerd already solved this once: [`src/pyodide/internal/patches/httpx.py`](https://github.com/cloudflare/workerd/blob/main/src/pyodide/internal/patches/httpx.py) monkey-patches `AsyncClient._send_single_request()` to bypass jsfetch.py entirely, passing all headers via `js.fetch()` directly. But that patch is gated to Pyodide 0.26.0a2 only ([`python-entrypoint-helper.ts`](https://github.com/cloudflare/workerd/blob/main/src/pyodide/python-entrypoint-helper.ts)) and is not applied for Pyodide 0.27+.

Options:

1. **Extend the workerd monkey-patch to Pyodide 0.27+.** Re-apply `httpx_patch.py` for all Pyodide versions, not just 0.26.
2. **Patch the fork.** Set `HEADERS_TO_IGNORE = ()` in the hoodmane/httpx fork when running in Workers (not a browser). Workers set `sys.platform == "emscripten"` like browsers, so a Workers-specific environment check would be needed.
3. **Patch at install time.** Have `pywrangler` post-process the installed httpx to clear `HEADERS_TO_IGNORE` before bundling into `python_modules/`.

Option 1 is the smallest change — the patch already exists and works.

## Workaround

Use `js.fetch()` directly for outbound HTTP that needs `User-Agent`:

```python
from js import Object, fetch
from pyodide.ffi import to_js

opts = to_js(
    {"method": "GET", "headers": {"User-Agent": "myapp/1.0"}},
    dict_converter=Object.fromEntries,  # required: without this, dicts become JS Maps
)
response = await fetch(url, opts)
```

Or patch the bundled transport locally: set `HEADERS_TO_IGNORE = ()` in `python_modules/httpx/_transports/jsfetch.py` line 57.
