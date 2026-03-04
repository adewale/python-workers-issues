import json
import os
import re
import subprocess
import time
import socket
import sys
import pytest
from pathlib import Path

from contextlib import contextmanager

REPO_ROOT = Path(__file__).parents[1]


def pytest_addoption(parser):
    parser.addoption(
        "--deployed-url",
        action="store",
        default=None,
        help="Base URL of a deployed Worker (e.g. https://my-worker.workers.dev)",
    )
    parser.addoption(
        "--deploy",
        action="store_true",
        default=False,
        help="Deploy the Worker before running tests that need a deployed URL",
    )


def find_free_port():
    """Find an unused port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@contextmanager
def pywrangler_dev_server(directory: str):
    """Context manager to start and stop pywrangler dev server."""
    port = find_free_port()

    process = subprocess.Popen(
        ["uv", "run", "pywrangler", "dev", "--port", str(port)],
        cwd=REPO_ROOT / directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Wait for server to be ready
    ready = False
    timeout = 30
    if "CI" in os.environ and directory.startswith("2-"):
        # Starting the server the first time takes a really long time in CI.
        timeout = 300

    start_time = time.time()

    while not ready and time.time() - start_time < timeout:
        line = process.stdout.readline()
        if line:
            print(line.rstrip(), file=sys.stdout)  # Also print to stdout
            if "[wrangler:info] Ready on" in line:
                ready = True
                break
        time.sleep(0.1)

    if not ready:
        process.terminate()
        raise RuntimeError(f"Server failed to start within {timeout} seconds")

    try:
        yield port
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture
def dev_server(request):
    """Fixture that starts a dev server for the appropriate directory based on test name."""
    if request.node.get_closest_marker("skip") or request.node.get_closest_marker(
        "xfail"
    ):
        yield
        return

    test_name = request.node.name
    # Extract directory name from test name (e.g., "test_1_r2_binary" -> "1-r2-binary")
    dir_name = test_name.replace("test_", "").replace("_", "-")

    with pywrangler_dev_server(dir_name) as port:
        yield port


def _deploy_worker(directory: str) -> str:
    """Deploy a Worker, wait for it to be ready, and return its URL."""
    import requests as _requests

    result = subprocess.run(
        ["uv", "run", "pywrangler", "deploy"],
        cwd=REPO_ROOT / directory,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + result.stderr
    # pywrangler deploy prints: https://worker-name.subdomain.workers.dev
    match = re.search(r"(https://[\w.-]+\.workers\.dev)", output)
    if not match:
        raise RuntimeError(
            f"Could not find workers.dev URL in deploy output:\n{output}"
        )
    url = match.group(1)

    # Wait for the worker to be reachable after deploy
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            resp = _requests.get(url, timeout=5)
            if resp.status_code < 500:
                return url
        except _requests.exceptions.ConnectionError:
            pass
        time.sleep(2)

    raise RuntimeError(f"Worker at {url} not ready within 30s after deploy")


def _worker_name_from_config(directory: str) -> str:
    """Read the worker name from wrangler.jsonc."""
    config_path = REPO_ROOT / directory / "wrangler.jsonc"
    text = config_path.read_text()
    # Strip // comments for JSON parsing
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    return json.loads(text)["name"]


@pytest.fixture
def deployed_url(request):
    """Base URL of a deployed Worker.

    Resolution order:
      1. --deployed-url flag or DEPLOYED_WORKER_URL env var (explicit)
      2. --deploy flag: runs pywrangler deploy and captures the URL
      3. Skip the test
    """
    url = request.config.getoption("--deployed-url") or os.environ.get(
        "DEPLOYED_WORKER_URL"
    )
    if url:
        return url.rstrip("/")

    test_name = request.node.name
    dir_name = test_name.replace("test_", "").replace("_", "-")

    if request.config.getoption("--deploy"):
        return _deploy_worker(dir_name)

    pytest.skip(
        "No deployed Worker URL (use --deployed-url, --deploy, or DEPLOYED_WORKER_URL)"
    )
