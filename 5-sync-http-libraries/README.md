# Sync HTTP Libraries Work in Python Workers

This example verifies that synchronous Python HTTP clients can make outbound requests from a Python Worker.

Older guidance said libraries like `requests` and `urllib3` failed in Python Workers with errors such as `blocking call in async context`. Current Python Workers support this pattern.

## What it tests

`GET /test` calls `https://httpbin.org/headers` using both:

- `requests.get(...)`
- `urllib3.PoolManager().request(...)`

Both calls run directly inside the Worker's request handler. The response includes the status code and the headers httpbin observed.

## Run

```bash
uv run pywrangler dev
# GET http://localhost:8787/test
```

Expected result: both clients return HTTP 200 and preserve the test headers.
