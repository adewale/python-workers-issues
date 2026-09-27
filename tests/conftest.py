import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
import socket
import sys
import pytest
from pathlib import Path

from contextlib import contextmanager

REPO_ROOT = Path(__file__).parents[1]

# Seconds to wait for `pywrangler dev` to print "Ready on". The same budget applies
# to every repro directory. Cold starts vendor packages and fetch wrangler via npx,
# so CI (which pre-warms each directory in its install step) keeps the 300 s budget
# it has always given the first directory. Override with PYWRANGLER_DEV_TIMEOUT.
DEFAULT_DEV_TIMEOUT = 300 if "CI" in os.environ else 30


def dev_server_timeout() -> float:
    value = os.environ.get("PYWRANGLER_DEV_TIMEOUT")
    return float(value) if value else float(DEFAULT_DEV_TIMEOUT)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print a clear summary of which platform bugs were confirmed."""
    xfailed = terminalreporter.stats.get("xfailed", [])
    passed = terminalreporter.stats.get("passed", [])
    failed = terminalreporter.stats.get("failed", [])
    if not xfailed and not passed:
        return

    terminalreporter.section("Results")
    for report in xfailed:
        terminalreporter.line(f"  CONFIRMED  {report.wasxfail}")
    for report in passed:
        name = report.nodeid.split("::")[-1]
        terminalreporter.line(f"  PASSED     {name}")
    for report in failed:
        name = report.nodeid.split("::")[-1]
        if "XPASS(strict)" in str(report.longrepr):
            terminalreporter.line(
                f"  NOT REPRODUCED  {name}: the platform bug may be fixed upstream; "
                "move it to Resolved Issues and remove its xfail marker"
            )
        else:
            terminalreporter.line(f"  FAILED     {name}")


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

    # `uv run pywrangler dev` forks npx -> wrangler -> workerd. Start it in its own
    # process group so teardown can stop the whole tree; terminating only `uv`
    # leaves wrangler and workerd running after the test session ends.
    process = subprocess.Popen(
        ["uv", "run", "pywrangler", "dev", "--port", str(port)],
        cwd=REPO_ROOT / directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    # Drain the server's output on a thread for its whole lifetime, so a silent hang
    # still hits the timeout and a chatty server never blocks on a full pipe.
    lines: queue.Queue = queue.Queue()

    def _pump():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=_pump, daemon=True).start()

    timeout = dev_server_timeout()
    deadline = time.monotonic() + timeout
    ready = False
    while not ready:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            break
        if line is None:
            _stop_process_group(process)
            process.wait()
            raise RuntimeError(
                f"pywrangler dev in {directory} exited with code "
                f"{process.returncode} before it was ready"
            )
        print(line.rstrip(), file=sys.stdout)  # Also print to stdout
        if "[wrangler:info] Ready on" in line:
            ready = True

    if not ready:
        _stop_process_group(process)
        raise RuntimeError(
            f"Server in {directory} failed to start within {timeout:g} seconds "
            "(set PYWRANGLER_DEV_TIMEOUT to change the budget)"
        )

    try:
        yield port
    finally:
        _stop_process_group(process)


def _stop_process_group(process: subprocess.Popen) -> None:
    """Stop the dev server and every process it spawned (npx, wrangler, workerd)."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        process.poll()  # reap the group leader so it stops counting as a member
        try:
            os.killpg(process.pid, 0)  # raises once no process in the group remains
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


@pytest.fixture
def dev_server(request):
    """Fixture that starts a dev server for the appropriate directory based on test name."""
    # Only an explicit skip avoids starting the server. Active-issue tests are
    # marked xfail(strict=True) and must still run against a real server.
    if request.node.get_closest_marker("skip"):
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

    print(f"\nDeploying worker from {directory}/...", flush=True)
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
    print(f"Deployed to {url}", flush=True)

    # Wait for the worker to be reachable after deploy
    print("Waiting for worker to be reachable...", end="", flush=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            resp = _requests.get(url, timeout=5)
            if resp.status_code < 500:
                print(" ready.\n", flush=True)
                return url
        except _requests.exceptions.ConnectionError:
            pass
        print(".", end="", flush=True)
        time.sleep(2)

    raise RuntimeError(f"Worker at {url} not ready within 30s after deploy")


def _worker_name_from_config(directory: str) -> str:
    """Read the worker name from wrangler.jsonc."""
    config_path = REPO_ROOT / directory / "wrangler.jsonc"
    text = config_path.read_text()
    # Strip // comments for JSON parsing
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    return json.loads(text)["name"]


# Map test name prefixes to issue directories.
# The dev_server fixture derives directory from test name automatically
# (e.g. test_2_foo -> 2-foo), but deployed tests use split names like
# test_4a_foo that don't map to directory 4-r2-large-binary-roundtrip.
_TEST_DIR_OVERRIDES = {
    "4a": "4-r2-large-binary-roundtrip",
    "4b": "4-r2-large-binary-roundtrip",
    "4c": "4-r2-large-binary-roundtrip",
}


def _dir_for_test(test_name: str) -> str:
    """Resolve the issue directory for a test name."""
    prefix = test_name.replace("test_", "").split("_")[0]
    if prefix in _TEST_DIR_OVERRIDES:
        return _TEST_DIR_OVERRIDES[prefix]
    return test_name.replace("test_", "").replace("_", "-")


_deployed_urls: dict[str, str] = {}


@pytest.fixture
def deployed_url(request):
    """Base URL of a deployed Worker.

    Resolution order:
      1. --deployed-url flag or DEPLOYED_WORKER_URL env var (explicit)
      2. --deploy flag: runs pywrangler deploy and captures the URL
         (cached per directory so multiple tests share one deploy)
      3. Skip the test
    """
    url = request.config.getoption("--deployed-url") or os.environ.get(
        "DEPLOYED_WORKER_URL"
    )
    if url:
        return url.rstrip("/")

    test_name = request.node.name
    dir_name = _dir_for_test(test_name)

    if request.config.getoption("--deploy"):
        if dir_name not in _deployed_urls:
            _deployed_urls[dir_name] = _deploy_worker(dir_name)
        return _deployed_urls[dir_name]

    pytest.skip(
        "No deployed Worker URL (use --deployed-url, --deploy, or DEPLOYED_WORKER_URL)"
    )
