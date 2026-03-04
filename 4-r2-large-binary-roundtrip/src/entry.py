"""R2 large binary round-trip — Pyodide FFI crash reproduction.

Demonstrates that returning large R2 objects (>~10MB) through a Python
ASGI response crashes the Worker.  The data must cross JS→Python
(ReadableStream → bytes via ``consume_readable_stream``) then Python→JS
(bytes → Response body), doubling memory usage in the Wasm linear memory.

The fix is to bypass Python entirely: pass R2's ``body`` ReadableStream
directly to a JS ``Response``.

Endpoints:
    POST /seed?size_mb=50  — Generate and store a large binary in R2
    GET  /broken/{key}     — BROKEN: round-trip through Python (crashes)
    GET  /fixed/{key}      — FIXED: R2 ReadableStream passed directly to JS Response
"""

import js
from fastapi import FastAPI, Request
from pyodide.ffi import to_js
from starlette.responses import Response
from workers import WorkerEntrypoint

app = FastAPI()


def _to_py_bytes(js_value):
    """Convert a JS Uint8Array chunk to Python bytes."""
    return bytes(js_value.to_py())


@app.get("/")
async def root():
    return {
        "example": "R2 Large Binary Round-Trip",
        "bug": "Returning large R2 objects through Python ASGI crashes the Worker",
        "endpoints": {
            "POST /seed?size_mb=50": "Generate and store a large binary in R2",
            "GET /broken/{key}": "BROKEN: round-trip through Python (crashes for large files)",
            "GET /fixed/{key}": "FIXED: R2 ReadableStream bypasses Python entirely",
        },
    }


@app.post("/seed")
async def seed_data(req: Request, size_mb: int = 50):
    """Generate and store a large binary in R2.

    The data is a repeating byte pattern — no external files needed.
    """
    env = req.scope["env"]
    r2 = env.BUCKET

    data = bytes(range(256)) * (size_mb * 1024 * 1024 // 256)
    key = f"test-{size_mb}mb"

    js_view = to_js(data)
    js_owned = js_view.slice()
    await r2.put(key, js_owned)

    return {"stored_bytes": len(data), "key": key}


# --------------------------------------------------------------------------
# BUG: Large R2 binary round-trip through Python crashes the Worker.
#
# When an R2 object's body is read into Python bytes (via getReader() or
# arrayBuffer()), then returned as a FastAPI Response, the data crosses
# the FFI boundary TWICE:
#   1. JS ReadableStream → Python bytes  (JS→Python)
#   2. Python bytes → JS Response body   (Python→JS)
#
# For large files (>~10MB), this doubles memory usage in the Wasm linear
# memory and crashes the Worker with a generic error page.
# --------------------------------------------------------------------------


@app.get("/broken/{key}")
async def broken_read(key: str, req: Request):
    """BROKEN: Read R2 body into Python, return as Response.

    This round-trips the data through Python memory, which crashes
    for large files (>~10MB).
    """
    env = req.scope["env"]
    r2 = env.BUCKET
    obj = await r2.get(key)
    if obj is None:
        return Response(content="Not found", status_code=404)

    # Read ALL chunks into Python memory (JS→Python)
    reader = obj.body.getReader()
    parts = []
    try:
        while True:
            result = await reader.read()
            if result.done:
                break
            chunk = result.value
            if chunk is not None:
                parts.append(_to_py_bytes(chunk))
    finally:
        reader.releaseLock()

    full_body = b"".join(parts)

    # Return via ASGI Response (Python→JS) — crashes for large data
    return Response(
        content=full_body,
        media_type="application/octet-stream",
        headers={"content-length": str(len(full_body))},
    )


class Default(WorkerEntrypoint):
    """Worker entrypoint with both broken (ASGI) and fixed (JS bypass) paths."""

    async def fetch(self, request):
        import asgi

        url = request.url
        path = js.URL.new(url).pathname

        # FIXED path: bypass Python entirely for /fixed/{key}
        if path.startswith("/fixed/"):
            key = path[len("/fixed/"):]
            if not key:
                return js.Response.new("Missing key", {"status": 400})

            obj = await self.env.BUCKET.get(key)
            if obj is None:
                return js.Response.new("Not found", {"status": 404})

            # Pass R2 ReadableStream directly to JS Response — data never
            # enters Python memory.
            headers = to_js({
                "content-type": "application/octet-stream",
            })
            return js.Response.new(obj.body, headers=headers)

        # All other routes go through FastAPI (ASGI)
        return await asgi.fetch(app, request.js_object, self.env)
