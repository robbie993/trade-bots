"""Hosting the village without handing out its controls.

Every page in this repository says *this page has no authentication; bind it to
localhost*. Deploying it to Vercel makes that warning load-bearing: the
approval gate becomes a public URL where an unauthenticated POST grants a
capital allocation. So a hosted deployment shows everything and changes
nothing, and these tests hold that line from both directions — writes refused
when hosted, and nothing whatsoever changed when not.
"""

from __future__ import annotations

import importlib

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from src import deploy  # noqa: E402


@pytest.fixture
def village(tmp_path, firms_yaml, monkeypatch):
    """A configured app whose mode is decided per test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'd.db'}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    from src.cli import main

    main(["trade", "init"])

    def build():
        import src.agents.web as web

        importlib.reload(deploy)
        importlib.reload(web)
        return TestClient(web.app, raise_server_exceptions=False)

    yield build
    # Leave the module in its default (local) shape for everything after.
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("MVV_PUBLIC", "0")
    build()


# =========================================================================
# which mode
# =========================================================================
def test_a_laptop_is_not_public(monkeypatch):
    for marker in deploy.HOSTED_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    assert deploy.is_public() is False


def test_a_serverless_host_is_public(monkeypatch):
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert deploy.is_public() is True


def test_the_platform_guess_can_be_overridden_either_way(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("MVV_PUBLIC", "0")
    assert deploy.is_public() is False
    monkeypatch.delenv("VERCEL")
    monkeypatch.setenv("MVV_PUBLIC", "1")
    assert deploy.is_public() is True


def test_sqlite_on_a_serverless_host_is_not_durable(monkeypatch):
    monkeypatch.setenv("MVV_PUBLIC", "1")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/mvv.db")
    assert deploy.storage_is_durable() is False
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@host/db")
    assert deploy.storage_is_durable() is True


def test_a_database_attached_under_the_hosts_own_name_counts(monkeypatch):
    """Vercel and Neon set POSTGRES_URL, not DATABASE_URL.

    Reading only DATABASE_URL meant attaching a database appeared to do
    nothing and the app fell back to SQLite on a disk that does not persist —
    the worst failure available, because it looks like it worked.
    """
    monkeypatch.setenv("MVV_PUBLIC", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@host/db")
    assert deploy.storage_is_durable() is True

    from src.config import Config

    # `postgres://` is normalised: psycopg wants `postgresql://`.
    assert Config().database_url == "postgresql://u:p@host/db"


def test_an_explicit_database_url_still_wins(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@host/db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./chosen.db")
    from src.config import Config

    assert Config().database_url == "sqlite:///./chosen.db"


# =========================================================================
# hosted: shows everything, changes nothing
# =========================================================================
def test_every_write_is_refused_when_hosted(village, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    client = village()

    assert client.post("/approvals/1/approve", data={"approved_by": "anyone"}).status_code == 403
    assert client.post("/village/actions/tick").status_code == 403
    assert client.post("/village/actions/apply-approvals").status_code == 403
    assert client.post("/village/actions/sandbox", data={"action": "form"}).status_code == 403


def test_the_refusal_says_where_the_real_console_is(village, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    body = village().post("/village/actions/tick").text
    assert "read-only" in body.lower()
    assert "src.main serve" in body


def test_the_controls_are_removed_not_left_to_fail(village, monkeypatch):
    """A button that 403s when clicked is worse than no button."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    client = village()
    for path in ("/", "/village", "/village/firms/alpha"):
        body = client.get(path).text
        assert "<form" not in body, path
        assert "<button" not in body, path
        assert "type=file" not in body, path


def test_reading_still_works_when_hosted(village, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    client = village()
    for path in ("/", "/village", "/village/flow", "/api/status", "/api/firms"):
        assert client.get(path).status_code == 200, path


def test_the_page_says_it_is_a_mirror(village, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    body = village().get("/village").text
    assert "Read-only mirror" in body


def test_an_ephemeral_database_is_called_out(village, monkeypatch):
    """Serverless disks do not persist; SQLite there loses every write."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    body = village().get("/village").text
    assert "No database" in body
    assert "DATABASE_URL" in body


# =========================================================================
# local: nothing changes
# =========================================================================
def test_nothing_is_blocked_locally(village, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("MVV_PUBLIC", "0")
    client = village()
    assert client.post("/village/actions/tick", follow_redirects=False).status_code == 303


def test_the_controls_are_all_there_locally(village, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("MVV_PUBLIC", "0")
    body = village().get("/village").text
    assert "<form" in body
    assert "<button" in body
    assert "Read-only mirror" not in body


# =========================================================================
# the stripping itself
# =========================================================================
def test_stripping_leaves_navigation_alone():
    """Links are not writes; removing them would make the mirror unusable."""
    html = (
        "<p><a href='/village/firms/alpha'>alpha</a></p>"
        "<form method=post action='/x'><button>Run</button></form>"
        "<input type=file name=f>"
    )
    out = deploy.strip_controls(html)
    assert "<a href='/village/firms/alpha'>alpha</a>" in out
    assert "<form" not in out and "<button" not in out and "type=file" not in out


def test_the_vercel_entrypoint_is_declared():
    """Vercel found several FastAPI apps and could not choose. This names one."""
    import tomllib
    from pathlib import Path

    config = tomllib.loads(Path("pyproject.toml").read_text())
    assert config["tool"]["vercel"]["entrypoint"] == "src.agents.web:app"
