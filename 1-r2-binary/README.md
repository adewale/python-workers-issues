# R2 Binary Data — `to_js()` Memory Detachment Bug

## The Bug

`to_js(bytes)` creates a `Uint8Array` **view** into Wasm linear memory, not an independent copy. If the Wasm heap grows (from allocations or garbage collection) while an async operation like `r2.put()` is in flight, the underlying `ArrayBuffer` becomes detached and R2 silently writes truncated or corrupted data.

```python
# DO NOT DO THIS:
js_view = to_js(data)
await r2.put("my-key", js_view)  # May silently truncate!
```

## The Fix

Call `.slice()` after `to_js()` to create an independent copy in the JS heap:

```python
js_view = to_js(data)      # Uint8Array VIEW into Wasm memory
js_owned = js_view.slice()  # Independent COPY in JS heap
await r2.put("my-key", js_owned)  # Safe!
```

## Run

```bash
uv run pywrangler dev
# Visit http://localhost:8787/store to write 64KB to R2 and verify the round-trip
```
