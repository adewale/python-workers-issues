"""R2 large binary round-trip — two distinct Pyodide FFI bugs.

This Worker isolates two independent bugs that affect returning R2 data
through Python Workers:

Bug 1 — ASGI adapter truncates StreamingResponse (any size):
    The Workers ASGI adapter only consumes the FIRST yielded chunk from an
    async generator, silently dropping all subsequent chunks.  This happens
    even for a 256KB file.

Bug 2 — FFI double-crossing exhausts Wasm memory (>~10MB):
    Reading R2 body into Python bytes, then returning via Response, creates
    three simultaneous copies (R2 buffer + Python bytes + JS Response body).
    Works for small files, crashes the Worker for >~10MB.

Workaround — stay on the JS side:
    Intercept the request in fetch() before ASGI and pass R2's body
    ReadableStream directly to a JS Response.  Data never enters Python.

Endpoints:
    POST /seed?size_mb=50       — Store a large binary in R2
    POST /seed-small?size_kb=256 — Store a small binary in R2
    GET  /asgi-full-body/{key}  — Bug 2: reads all chunks into Python, returns via Response
    GET  /streaming/{key}       — Bug 1: async generator + StreamingResponse (truncated)
    GET  /compare/{key}         — Diagnostic: JSON breakdown of what each path would return
    GET  /fixed/{key}           — Workaround: R2 ReadableStream passed directly to JS Response
"""

import json

import js
from fastapi import FastAPI, Request
from pyodide.ffi import to_js
from starlette.responses import JSONResponse, Response, StreamingResponse
from workers import WorkerEntrypoint

app = FastAPI()


def _to_py_bytes(js_value):
    """Convert a JS Uint8Array chunk to Python bytes."""
    return bytes(js_value.to_py())


async def _read_all_chunks(r2_body):
    """Read all chunks from an R2 ReadableStream into Python.

    Returns (parts, chunk_sizes) where parts is a list of bytes objects.
    """
    reader = r2_body.getReader()
    parts = []
    chunk_sizes = []
    try:
        while True:
            result = await reader.read()
            if result.done:
                break
            chunk = result.value
            if chunk is not None:
                py_bytes = _to_py_bytes(chunk)
                parts.append(py_bytes)
                chunk_sizes.append(len(py_bytes))
    finally:
        reader.releaseLock()
    return parts, chunk_sizes


# --------------------------------------------------------------------------
# Root — endpoint index
# --------------------------------------------------------------------------


@app.get("/")
async def root():
    return {
        "example": "R2 Large Binary Round-Trip",
        "bugs": {
            "bug_1": "ASGI adapter truncates StreamingResponse to first chunk (any size)",
            "bug_2": "FFI double-crossing exhausts Wasm memory for large files (>~10MB)",
        },
        "endpoints": {
            "POST /seed?size_mb=50": "Store a large binary in R2",
            "POST /seed-small?size_kb=256": "Store a small binary in R2",
            "GET /asgi-full-body/{key}": "Bug 2: round-trip through Python (crashes for large files)",
            "GET /streaming/{key}": "Bug 1: StreamingResponse truncated to first chunk",
            "GET /compare/{key}": "Diagnostic: JSON breakdown of all paths",
            "GET /fixed/{key}": "Workaround: R2 ReadableStream bypasses Python entirely",
        },
    }


# --------------------------------------------------------------------------
# Seed endpoints
# --------------------------------------------------------------------------


@app.post("/seed")
async def seed_data(req: Request, size_mb: int = 50):
    """Generate and store a large binary in R2."""
    env = req.scope["env"]
    r2 = env.BUCKET

    data = bytes(range(256)) * (size_mb * 1024 * 1024 // 256)
    key = f"test-{size_mb}mb"

    js_view = to_js(data)
    js_owned = js_view.slice()
    await r2.put(key, js_owned)

    return {"stored_bytes": len(data), "key": key}


@app.post("/seed-small")
async def seed_small(req: Request, size_kb: int = 256):
    """Generate and store a small binary in R2 for testing truncation."""
    env = req.scope["env"]
    r2 = env.BUCKET

    data = bytes(range(256)) * (size_kb * 1024 // 256)
    key = f"test-{size_kb}kb"

    js_view = to_js(data)
    js_owned = js_view.slice()
    await r2.put(key, js_owned)

    return {"stored_bytes": len(data), "key": key}


# --------------------------------------------------------------------------
# Bug 2: Full body read — works for small files, crashes for large ones
# --------------------------------------------------------------------------


@app.get("/asgi-full-body/{key}")
async def asgi_full_body(key: str, req: Request):
    """Read R2 body into Python, return as Response.

    Data crosses the FFI boundary twice:
      1. JS ReadableStream → Python bytes  (JS→Python via getReader)
      2. Python bytes → ASGI Response body  (Python→JS)

    Works for small files.  For large files (>~10MB), the three
    simultaneous copies (R2 buffer + Python bytes + JS Response body)
    exhaust Wasm linear memory and crash the Worker.
    """
    env = req.scope["env"]
    r2 = env.BUCKET
    obj = await r2.get(key)
    if obj is None:
        return Response(content="Not found", status_code=404)

    parts, chunk_sizes = await _read_all_chunks(obj.body)
    full_body = b"".join(parts)

    diag = {
        "chunk_count": len(parts),
        "total_bytes_in_python": len(full_body),
        "peak_copies": "3: R2 buffer + Python bytes + JS Response body",
    }

    return Response(
        content=full_body,
        media_type="application/octet-stream",
        headers={
            "content-length": str(len(full_body)),
            "x-ffi-diag": json.dumps(diag),
        },
    )


# --------------------------------------------------------------------------
# Bug 1: StreamingResponse truncation — broken at any size
# --------------------------------------------------------------------------


@app.get("/streaming/{key}")
async def streaming_read(key: str, req: Request):
    """Return R2 body via StreamingResponse with an async generator.

    The Workers ASGI adapter only consumes the FIRST yielded chunk,
    silently dropping all subsequent chunks.  The response will be
    truncated to ~64KB (one R2 chunk) regardless of the actual file size.
    """
    env = req.scope["env"]
    r2 = env.BUCKET
    obj = await r2.get(key)
    if obj is None:
        return Response(content="Not found", status_code=404)

    async def chunk_generator():
        reader = obj.body.getReader()
        try:
            while True:
                result = await reader.read()
                if result.done:
                    break
                chunk = result.value
                if chunk is not None:
                    yield _to_py_bytes(chunk)
        finally:
            reader.releaseLock()

    return StreamingResponse(
        chunk_generator(),
        media_type="application/octet-stream",
    )


# --------------------------------------------------------------------------
# Diagnostic: compare all paths for a given key
# --------------------------------------------------------------------------


@app.get("/compare/{key}")
async def compare_paths(key: str, req: Request):
    """Return JSON diagnostics showing what each path would return.

    Reads all chunks once and reports what each endpoint path would
    produce, without actually going through their response paths.
    """
    env = req.scope["env"]
    r2 = env.BUCKET
    obj = await r2.get(key)
    if obj is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    parts, chunk_sizes = await _read_all_chunks(obj.body)
    total_size = sum(chunk_sizes)
    first_chunk_size = chunk_sizes[0] if chunk_sizes else 0

    return JSONResponse({
        "key": key,
        "r2_body_size": total_size,
        "chunk_count": len(parts),
        "chunk_sizes": chunk_sizes,
        "streaming_would_return": first_chunk_size,
        "full_body_would_return": total_size,
        "fixed_would_return": total_size,
        "ffi_crossings": {
            "/asgi-full-body": "JS→Python (getReader) then Python→JS (Response) = 2 crossings",
            "/streaming": "JS→Python (getReader) then Python→JS (StreamingResponse) = 2 crossings, but adapter truncates after first yield",
            "/fixed": "JS→JS (R2 body → Response) = 0 crossings, data never enters Python",
        },
    })


# --------------------------------------------------------------------------
# Worker entrypoint
# --------------------------------------------------------------------------


class Default(WorkerEntrypoint):
    """Worker entrypoint with ASGI routes and a JS-bypass for /fixed/."""

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
