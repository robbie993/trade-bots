"""The deployment entrypoint, and the fallback behind it.

Three rounds were spent guessing at one `FUNCTION_INVOCATION_FAILED`, and each
guess failed the same way: the code meant to explain the problem could only run
if the application had already loaded. So the property under test here is that
the explanation survives the application not loading at all — including the
case where the reason is that its dependencies are missing, which is exactly
when a fallback built on those dependencies would say nothing.
"""

from __future__ import annotations

import asyncio
import importlib
import sys

import pytest

import src.asgi as asgi


def drive(app, path: str = "/") -> tuple:
    """Call an ASGI app the way a server would, and collect what it sent."""
    sent: list = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    asyncio.run(app({"type": "http", "path": path}, receive, send))
    return sent[0], b"".join(m.get("body", b"") for m in sent[1:])


@pytest.fixture
def without(monkeypatch):
    """Reload the entrypoint with a package made unimportable."""
    def blocked(package: str):
        class Blocker:
            def find_module(self, name, path=None):
                return self if name.split(".")[0] == package else None

            def load_module(self, name):
                raise ModuleNotFoundError(f"No module named {package!r}")

        for name in [n for n in sys.modules
                     if n.startswith((package, "src.agents", "src.trading"))]:
            monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setattr(sys, "meta_path", [Blocker(), *sys.meta_path])
        return importlib.reload(asgi)

    yield blocked
    importlib.reload(asgi)      # leave the module holding the real app


# =========================================================================
# the happy path
# =========================================================================
def test_the_entrypoint_is_the_real_app_when_it_imports():
    module = importlib.reload(asgi)
    assert module._ERROR == ""
    assert module.app is module._APP
    assert module.app is not module._report


def test_the_declared_entrypoint_points_here():
    """Vercel loads this module, not the app directly — that is the point."""
    import tomllib
    from pathlib import Path

    config = tomllib.loads(Path("pyproject.toml").read_text())
    assert config["tool"]["vercel"]["entrypoint"] == "src.asgi:app"


# =========================================================================
# the fallback
# =========================================================================
def test_a_missing_dependency_is_reported_rather_than_crashing(without):
    """The case a FastAPI-based fallback could never report."""
    module = without("fastapi")
    assert module.app is module._report
    assert "No module named 'fastapi'" in module._ERROR

    start, body = drive(module.app)
    assert start["status"] == 500
    assert dict(start["headers"])[b"content-type"].startswith(b"text/html")
    assert b"No module named" in body
    assert b"did not start" in body


def test_a_missing_dependency_is_named_as_such(without):
    """A traceback is evidence; saying what it means saves a round trip."""
    module = without("fastapi")
    _, body = drive(module.app)
    assert b"never installed" in body
    assert b"requirements.txt" in body


def test_an_ordinary_failure_gets_no_dependency_hint():
    assert asgi._hint("ValueError: something else entirely") == ""
    assert asgi._hint("ModuleNotFoundError: No module named 'fastapi'") != ""


def test_the_traceback_is_escaped_into_the_page(without):
    module = without("fastapi")
    module._ERROR = "<script>alert(1)</script>"
    _, body = drive(module.app)
    assert b"<script>alert" not in body
    assert b"&lt;script&gt;" in body


def test_the_api_gets_json_not_a_page(without):
    module = without("fastapi")
    start, body = drive(module.app, "/api/status")
    assert dict(start["headers"])[b"content-type"] == b"application/json"

    import json

    payload = json.loads(body)
    assert payload["ok"] is False
    assert "No module named" in payload["traceback"]


def test_the_json_escaping_survives_a_real_traceback(without):
    import json

    module = without("fastapi")
    module._ERROR = 'line "one"\n\tline\\two\n\x01'
    _, body = drive(module.app, "/api/status")
    assert json.loads(body)["traceback"] == module._ERROR


def test_the_lifespan_handshake_is_answered(without):
    """An unanswered startup message can hang the server."""
    module = without("fastapi")
    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent: list = []

    async def send(message):
        sent.append(message["type"])

    async def receive():
        return next(events)

    asyncio.run(module.app({"type": "lifespan"}, receive, send))
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


def test_a_non_http_scope_is_ignored_quietly(without):
    module = without("fastapi")
    sent: list = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {}

    asyncio.run(module.app({"type": "websocket"}, receive, send))
    assert sent == []
