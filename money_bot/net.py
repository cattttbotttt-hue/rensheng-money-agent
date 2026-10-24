"""Minimal HTTP GET with retries (stdlib urllib)."""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from . import config


def fetch(url: str, timeout: int = config.HTTP_TIMEOUT, attempts: int = 2) -> bytes:
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # noqa: PERF203
            last_err = exc
            if i + 1 < attempts:
                time.sleep(3 * (i + 1))
    raise RuntimeError(f"fetch failed after {attempts} attempts: {url}: {last_err}")


def fetch_json(url: str, **kwargs):
    import json

    return json.loads(fetch(url, **kwargs).decode("utf-8", "replace"))
