# Python Workers Issues

Self-contained reproductions of [Python Workers](https://developers.cloudflare.com/workers/languages/python) bugs. Each numbered directory is an independent project that demonstrates a specific issue and its workaround.

## Get started

1. `git clone https://github.com/cloudflare/python-workers-issues`
2. `cd` into an issue directory (e.g. `cd 1-r2-binary`)
3. `uv run pywrangler dev`
4. Press the `b` key to open a browser tab and make a request to the Worker

## Issues

- [**`1-r2-binary/`**](1-r2-binary) — `to_js(bytes)` creates a Wasm memory *view*, not a copy. If the Wasm heap grows during an async R2 write, the `ArrayBuffer` detaches and data is silently truncated. **Fix:** call `.slice()` after `to_js()`.
- [**`2-fastapi-r2-streaming/`**](2-fastapi-r2-streaming) — The Workers ASGI adapter only consumes the first yielded chunk from `StreamingResponse` async generators, silently truncating R2 content larger than ~4 KB. **Workaround:** read the full body and return a plain `Response`.

## Open Beta and Limits

- Python Workers are in open beta. You can use packages in your Workers by using the [pywrangler](https://github.com/cloudflare/workers-py?tab=readme-ov-file#pywrangler) tool.
- You must add the `python_workers` compatibility flag to your Worker while Python Workers are in open beta.

We'd love your feedback. Join the `#python-workers channel` in the [Cloudflare Developers Discord](https://discord.cloudflare.com/) and let us know what you'd like to see next.

## License

The [Apache 2.0 license](LICENSE.md).
