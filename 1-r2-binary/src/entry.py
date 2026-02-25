"""R2 binary data example — writing bytes to R2 from a Python Worker.

Demonstrates a subtle Pyodide FFI bug: ``to_js(bytes)`` creates a
``Uint8Array`` *view* into Wasm linear memory, not an independent copy.
If the Wasm heap grows while an async R2 write is in flight, the
underlying ``ArrayBuffer`` becomes detached and R2 silently writes
truncated data.

The fix is to call ``.slice()`` after ``to_js()`` to create an owned
copy in the JS heap that survives memory growth.
"""

from pyodide.ffi import to_js
from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = request.url
        path = url.split("//", 1)[-1].split("/", 1)[-1] if "//" in url else url
        path = "/" + path if not path.startswith("/") else path
        # Strip query string
        path = path.split("?")[0]

        if path == "/store":
            return await self._store_binary()
        elif path.startswith("/read/"):
            key = path[len("/read/") :]
            return await self._read_binary(key)
        else:
            return Response(
                "R2 Binary Data Example\n\n"
                "GET /store       - Write binary data to R2 and verify round-trip\n"
                "GET /read/<key>  - Read binary data back from R2\n",
                headers={"content-type": "text/plain"},
            )

    async def _store_binary(self):
        r2 = self.env.BUCKET

        # Generate 64KB of binary data — large enough that Wasm memory
        # growth during the async r2.put() can cause ArrayBuffer detachment.
        data = bytes(range(256)) * 256  # 64KB repeating pattern

        # ----------------------------------------------------------------
        # BUG: to_js(bytes) creates a Uint8Array VIEW into Wasm linear
        # memory — NOT a copy.  If the Wasm heap grows (from allocations
        # or garbage collection) while an async operation like r2.put()
        # is in flight, the underlying ArrayBuffer becomes detached.
        # R2 then silently writes truncated or corrupted data.
        #
        # DO NOT DO THIS:
        #
        #   js_view = to_js(data)
        #   await r2.put("my-key", js_view)  # May silently truncate!
        #
        # ----------------------------------------------------------------

        # ----------------------------------------------------------------
        # FIX: Call .slice() after to_js() to create an independent copy
        # in the JS heap.  Uint8Array.prototype.slice() allocates a new
        # ArrayBuffer that is not affected by Wasm memory growth.
        # ----------------------------------------------------------------
        js_view = to_js(data)  # Uint8Array VIEW into Wasm memory
        js_owned = js_view.slice()  # Independent COPY in JS heap
        await r2.put("test-data", js_owned)

        # Read it back and verify the round-trip
        obj = await r2.get("test-data")
        if obj is None:
            return Response.json({"error": "Failed to read back from R2"}, status=500)

        readback = await obj.arrayBuffer()
        readback_bytes = bytes(readback.to_py())

        result = {
            "original_size": len(data),
            "stored_size": len(readback_bytes),
            "data_matches": readback_bytes == data,
            "fix": "Always call .slice() after to_js(bytes) before passing to async JS APIs",
        }

        return Response.json(result)

    async def _read_binary(self, key):
        r2 = self.env.BUCKET
        obj = await r2.get(key)
        if obj is None:
            return Response("Not found", status=404)

        body = await obj.arrayBuffer()
        body_bytes = bytes(body.to_py())

        return Response(
            body_bytes,
            headers={
                "content-type": "application/octet-stream",
                "content-length": str(len(body_bytes)),
            },
        )
