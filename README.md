# Python Workers Issues

Self-contained reproductions of [Python Workers](https://developers.cloudflare.com/workers/languages/python) bugs. Each numbered directory is an independent project that demonstrates a specific issue and its workaround.

## Get started

1. `git clone https://github.com/cloudflare/python-workers-issues`
2. `cd` into an issue directory (e.g. `cd 2-fastapi-r2-streaming`)
3. `uv run pywrangler dev`
4. Press the `b` key to open a browser tab and make a request to the Worker

## Issues

- [**`2-fastapi-r2-streaming/`**](2-fastapi-r2-streaming) — The Workers ASGI adapter only consumes the first yielded chunk from `StreamingResponse` async generators, silently truncating R2 content larger than ~4 KB. **Workaround:** read the full body and return a plain `Response`.
- [**`3-httpx-headers/`**](3-httpx-headers) — The pywrangler-bundled httpx replaces httpcore with a `jsfetch.py` transport that strips the `User-Agent` header to avoid browser CORS preflights. Workers aren't browsers — this causes 403s from APIs like GitHub that require `User-Agent`. **Workaround:** use `js.fetch()` directly.
- [**`4-r2-large-binary-roundtrip/`**](4-r2-large-binary-roundtrip) — Returning large R2 objects (>~10MB) through a Python ASGI response crashes the Worker. The data crosses the Pyodide FFI boundary twice (JS→Python→JS), doubling memory usage in Wasm linear memory. **Workaround:** bypass Python by passing R2's `body` ReadableStream directly to a JS `Response`.

## Open Beta and Limits

- Python Workers are in open beta. You can use packages in your Workers by using the [pywrangler](https://github.com/cloudflare/workers-py?tab=readme-ov-file#pywrangler) tool.
- You must add the `python_workers` compatibility flag to your Worker while Python Workers are in open beta.

We'd love your feedback. Join the `#python-workers channel` in the [Cloudflare Developers Discord](https://discord.cloudflare.com/) and let us know what you'd like to see next.

## License

The [Apache 2.0 license](LICENSE.md).
