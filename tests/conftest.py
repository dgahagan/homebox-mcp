"""Shared pytest fixtures for the homebox-mcp test suite.

SAFETY: the real module auto-loads a sibling `.env` with LIVE production
credentials at import time. We set fake HOMEBOX_* env vars *before* importing
the module so its module-level httpx.Client is built against an unreachable
test host, and we drive every request through respx (assert_all_mocked=True)
so an unmocked request fails the test instead of escaping to the network.
"""
from __future__ import annotations

import os

# --- MUST run before importing homebox_mcp: real env wins over .env via
# os.environ.setdefault, and _client's base_url is frozen at import time. ---
os.environ["HOMEBOX_URL"] = "http://homebox.test"
os.environ["HOMEBOX_TOKEN"] = "test-token"
os.environ["HOMEBOX_ALIAS_FIELD"] = "item_id"
os.environ.pop("HOMEBOX_LABEL_DIR", None)

import httpx  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402

import homebox_mcp as hb  # noqa: E402

BASE = "http://homebox.test/api/v1"


@pytest.fixture(autouse=True)
def _reset_version_cache():
    """The version guard caches its result module-wide; reset it per test."""
    hb._version_checked = False
    hb._version_error = None
    hb._LOCATION_TYPE_ID = None
    yield


@pytest.fixture
def router():
    """A respx router bound to the test base_url.

    assert_all_mocked=True: any unmocked request raises (nothing escapes).
    assert_all_called=False: tests may register a route only to assert it was
    NOT called (e.g. a DELETE that a confirm-gate should block).
    """
    with respx.mock(base_url=BASE, assert_all_mocked=True,
                    assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def mock_status(router):
    """Callable to register GET /status. Defaults to a supported version."""
    def _add(version: str = "v0.26.2", side_effect=None):
        route = router.get("/status")
        if side_effect is not None:
            return route.mock(side_effect=side_effect)
        return route.mock(
            return_value=httpx.Response(200, json={"build": {"version": version}})
        )
    return _add


@pytest.fixture
def good_status(mock_status):
    """Register a supported /status so tool calls pass the version guard."""
    mock_status()
