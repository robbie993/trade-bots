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
    """Serverless disks do not persist; SQLite there cannot be written."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    body = village().get("/village").text
    assert "does not update" in body
    assert "DATABASE_URL" in body


def test_a_page_that_rendered_never_claims_there_is_no_database(
    village, monkeypatch
):
    """The banner used to contradict the page it was sitting on top of.

    This notice is only ever reached after `database_ok()` has passed, so the
    firms below it were just read out of a database. Saying "No database"
    there was simply false — it asked `storage_is_durable()`, which judges the
    URL scheme rather than the database, and calls a working SQLite file
    not durable.
    """
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    body = village().get("/village").text

    assert "No database" not in body
    assert "alpha" in body          # the page did render, out of the database


# =========================================================================
# a database it cannot open
#
# The build went green and then every request returned
# FUNCTION_INVOCATION_FAILED. The handler opens the database before rendering
# anything, so on a host with no writable disk it raised while connecting —
# and a banner rendered *after* the handler never got written, because there
# was no response to write it into.
# =========================================================================
@pytest.fixture
def unopenable(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:////proc/nonexistent/mvv.db")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    import src.agents.web as web

    importlib.reload(deploy)
    importlib.reload(web)
    return TestClient(web.app, raise_server_exceptions=False)


def test_an_unopenable_database_explains_itself_instead_of_crashing(unopenable):
    for path in ("/", "/village", "/village/flow"):
        response = unopenable.get(path)
        assert response.status_code == 503, path
        assert "No database" in response.text, path


def test_the_api_says_it_in_json(unopenable):
    response = unopenable.get("/api/status")
    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["read_only"] is True


def test_writes_are_still_refused_before_anything_else(unopenable):
    """The 403 does not depend on the database being readable."""
    assert unopenable.post("/village/actions/tick").status_code == 403


def test_a_reachable_database_is_served_even_in_public_mode(village, monkeypatch):
    """Previewing the mirror locally must not report a missing database.

    The first version decided from the URL scheme alone, which refused a
    perfectly good SQLite file and would have served pages against a Postgres
    URL that does not answer. It is probed now.
    """
    monkeypatch.setenv("MVV_PUBLIC", "1")
    monkeypatch.delenv("VERCEL", raising=False)
    assert village().get("/village").status_code == 200


def test_an_empty_database_is_told_apart_from_a_missing_one(monkeypatch, tmp_path):
    """A freshly attached database connects fine and has no tables.

    The first version of the probe only checked that a connection opened,
    which an empty database does perfectly well — so the page rendered and
    then crashed on the first real query with `no such table: firms`. That is
    the state every new deployment starts in, so it needed its own answer and
    its own instruction.
    """
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'never-migrated.db'}")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    import src.agents.web as web

    importlib.reload(deploy)
    importlib.reload(web)
    client = TestClient(web.app, raise_server_exceptions=False)

    assert deploy.database_state() == "empty"
    response = client.get("/village")
    assert response.status_code == 503
    assert "database is empty" in response.text
    assert "init-db" in response.text          # and what to do about it
    assert client.get("/api/status").json()["error"] == "the database is empty"


def test_an_empty_database_is_re_probed_rather_than_remembered(tmp_path, firms_yaml, monkeypatch):
    """The normal startup order on a container host, which the first version
    of the cache broke: the web service boots before the worker has migrated,
    answers "empty" — correctly — and then has to notice when that stops being
    true. Caching it forever meant a page that was wrong until redeployed."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'later.db'}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    import src.agents.web as web

    importlib.reload(deploy)
    importlib.reload(web)
    client = TestClient(web.app, raise_server_exceptions=False)
    assert client.get("/village").status_code == 503

    # The worker comes up and migrates. Same web process, no restart.
    from src.cli import main

    main(["init-db"])
    main(["trade", "init"])

    deploy._PROBED_AT = 0.0                    # as if the retry window elapsed
    assert client.get("/village").status_code == 200
    assert deploy.database_state() == "ok"


def test_a_working_database_is_not_re_probed_every_request(tmp_path, firms_yaml, monkeypatch):
    """The other half: "ok" is still cached, or every page costs a round trip."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fine.db'}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    importlib.reload(deploy)
    from src.cli import main

    main(["init-db"])
    assert deploy.database_state() == "ok"

    deploy._PROBED_AT = 0.0
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nope")
    assert deploy.database_state() == "ok"     # cached, not re-asked
    assert deploy.database_state(force=True) == "unreachable"


def test_a_broken_database_is_explained_even_when_the_host_is_not_detected(
    monkeypatch, tmp_path
):
    """The guard must not depend on guessing the platform.

    Everything here used to be installed only when `is_public()` returned
    true, so when the platform guess was wrong the middleware was simply
    absent and the handler crashed exactly as it had before — which is the
    failure this module exists to prevent. Refusing writes is about being
    public; a database that cannot be opened is not.
    """
    for marker in deploy.HOSTED_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.setenv("MVV_PUBLIC", "0")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////proc/nonexistent/mvv.db")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    import src.agents.web as web

    importlib.reload(deploy)
    importlib.reload(web)
    client = TestClient(web.app, raise_server_exceptions=False)

    assert deploy.is_public() is False
    response = client.get("/village")
    assert response.status_code == 503
    # "did not answer", not "no writable disk": the ephemeral-storage
    # explanation is about serverless hosts and would be wrong here.
    assert "did not answer" in response.text
    # ...and no public-mode banner, because it is not public.
    assert "Read-only mirror" not in response.text


def test_a_runtime_only_marker_is_enough(monkeypatch):
    """VERCEL is set at build time; VERCEL_URL is what exists at invoke time."""
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    for marker in deploy.HOSTED_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.setenv("VERCEL_URL", "trade-bots.vercel.app")
    assert deploy.is_public() is True


def test_a_migrated_database_is_served(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'good.db'}")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    from src.cli import main

    main(["init-db"])
    import src.agents.web as web

    importlib.reload(deploy)
    importlib.reload(web)
    assert deploy.database_state() == "ok"
    assert TestClient(web.app).get("/village").status_code == 200


def test_an_unreachable_postgres_is_named_as_such(monkeypatch, tmp_path):
    monkeypatch.setenv("MVV_PUBLIC", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nope")
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    import src.agents.web as web

    importlib.reload(deploy)
    importlib.reload(web)
    response = TestClient(web.app, raise_server_exceptions=False).get("/village")
    assert response.status_code == 503
    assert "did not answer" in response.text


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


def test_a_button_that_is_really_a_link_keeps_its_link():
    """The bug this catches: the mirror deleted its own way into the village.

    Mission Control was reached through a <button> wrapped in an <a>, which is
    navigation wearing a button's clothes. The stripper saw a button and
    removed it, taking the label and the only prominent link with it — on the
    one page where a reader has nothing else to click.
    """
    html = "<a class='btn go' href='/village'><button>Mission Control</button></a>"
    out = deploy.strip_controls(html)

    assert "href='/village'" in out
    assert "Mission Control" in out
    assert "<button" not in out          # the element still goes


def test_the_hosted_mirror_can_still_reach_mission_control(village, monkeypatch):
    """End to end: the link survives on the page a visitor actually lands on."""
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    body = village().get("/").text

    assert "/village" in body
    assert "Mission Control" in body
    assert "<button" not in body


def test_the_vercel_entrypoint_goes_through_the_shim():
    """Not the app directly — see src/asgi.py and tests/test_asgi.py.

    Pointing the platform at the app means an import failure is reported as
    FUNCTION_INVOCATION_FAILED with the cause in a log. The shim loads the app
    and serves the traceback when it cannot.
    """
    import tomllib
    from pathlib import Path

    config = tomllib.loads(Path("pyproject.toml").read_text())
    assert config["tool"]["vercel"]["entrypoint"] == "src.asgi:app"
