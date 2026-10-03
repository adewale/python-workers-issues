# Sync HTTP Libraries Work in Python Workers

This example verifies that synchronous Python HTTP clients can make outbound requests from a Python Worker.

Older guidance said libraries like `requests` and `urllib3` failed in Python Workers with errors such as `blocking call in async context`. Current Python Workers support this pattern.

## What it tests

`GET /test` calls `https://httpbin.org/headers` using both:

- `requests.get(...)`
- `urllib3.PoolManager().request(...)`

Both calls run directly inside the Worker's request handler. The response includes the status code and the headers httpbin observed.

`GET /test?echo=http://127.0.0.1:<port>/headers` sends both requests to a loopback echo server instead of httpbin.org; non-loopback `echo` URLs are rejected. `tests/test_examples.py::test_5_sync_http_libraries` uses this with `tests/echo_server.py`, so the test asserts on the requests that actually arrived (one per library, with both headers) rather than on the Worker's own report, and needs no outbound network.

## Run

```bash
uv run pywrangler dev
# GET http://localhost:8787/test
```

Expected result: both clients return HTTP 200 and preserve the test headers.
