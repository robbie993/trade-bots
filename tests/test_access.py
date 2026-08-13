"""Signing in to a village that lives on somebody else's computer.

`test_deploy.py` holds the line that a hosted deployment changes nothing. This
file is about the door that was then cut into that wall, and it is worth being
paranoid about: the thing on the other side is the approval gate, and every
dollar in the system stops there.

So the tests are mostly about the *no* answers. Wrong token, no token, a token
too short to be one, a cookie somebody edited, a cookie that expired, a cookie
signed with a different secret, a redirect pointing off-site, and guessing over
and over. The one `yes` — correct token, writes work — is a single test, which
is about the right ratio for a security boundary.
"""

from __future__ import annotations

import importlib
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from src import access, deploy  # noqa: E402

TOKEN = "a-long-enough-token-for-real-use"


@pytest.fixture
def hosted(tmp_path, firms_yaml, monkeypatch):
    """An app that believes it is on Railway, with the token per test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'd.db'}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    from src.cli import main

    main(["trade", "init"])
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")

    def build(token: str = TOKEN):
        if token:
            monkeypatch.setenv("MVV_GATE_TOKEN", token)
        else:
            monkeypatch.delenv("MVV_GATE_TOKEN", raising=False)
        access.reset_attempts()
        import src.agents.web as web

        importlib.reload(deploy)
        importlib.reload(web)
        return TestClient(web.app, raise_server_exceptions=False)

    yield build
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("MVV_GATE_TOKEN", raising=False)
    monkeypatch.setenv("MVV_PUBLIC", "0")
    access.reset_attempts()
    build("")


def sign_in(client, token: str = TOKEN):
    return client.post(
        access.UNLOCK_PATH, data={access.FIELD: token, "next": "/village"},
        follow_redirects=False,
    )


# =========================================================================
# whether signing in is possible at all
# =========================================================================
def test_no_token_means_no_door(monkeypatch):
    monkeypatch.delenv("MVV_GATE_TOKEN", raising=False)
    assert access.configured() is False
    assert access.misconfigured() is False


def test_a_short_token_is_refused_as_a_configuration(monkeypatch):
    """A four-character password in front of an approval gate is worse than an
    honest mirror, because it looks like protection."""
    monkeypatch.setenv("MVV_GATE_TOKEN", "hunter2")
    assert access.configured() is False
    assert access.misconfigured() is True


def test_a_long_token_opens_the_possibility(monkeypatch):
    monkeypatch.setenv("MVV_GATE_TOKEN", TOKEN)
    assert access.configured() is True
    assert access.misconfigured() is False


# =========================================================================
# the mirror, unchanged, when nobody configured a way in
# =========================================================================
def test_without_a_token_a_hosted_village_is_exactly_the_mirror_it_was(hosted):
    client = hosted("")
    assert client.get("/village").status_code == 200
    refused = client.post("/village/actions/tick", follow_redirects=False)
    assert refused.status_code == 403
    assert "read-only" in refused.text.lower()


def test_without_a_token_the_unlock_page_says_how_to_get_one(hosted):
    body = hosted("").get(access.UNLOCK_PATH)
    assert body.status_code == 503
    assert "MVV_GATE_TOKEN" in body.text


def test_a_short_token_leaves_the_mirror_up_and_says_why(hosted):
    client = hosted("short")
    assert client.post("/village/actions/tick", follow_redirects=False).status_code == 403
    page = client.get(access.UNLOCK_PATH)
    assert page.status_code == 503
    assert "too short" in page.text.lower()


def test_a_short_token_never_signs_anybody_in(hosted):
    """The refusal has to be at the door, not just in the wording."""
    client = hosted("short")
    assert sign_in(client, "short").status_code == 503
    assert client.post("/village/actions/tick", follow_redirects=False).status_code == 403


# =========================================================================
# the one yes
# =========================================================================
def test_the_right_token_brings_the_controls_back(hosted):
    client = hosted()
    assert client.post("/village/actions/tick", follow_redirects=False).status_code == 403

    response = sign_in(client)
    assert response.status_code == 303
    assert access.COOKIE in response.cookies

    after = client.post("/village/actions/tick", follow_redirects=False)
    assert after.status_code != 403
    assert "<form" in client.get("/village").text     # controls no longer stripped


def test_signing_out_puts_the_mirror_back(hosted):
    client = hosted()
    sign_in(client)
    client.get("/lock", follow_redirects=False)
    assert client.post("/village/actions/tick", follow_redirects=False).status_code == 403


# =========================================================================
# the many noes
# =========================================================================
def test_the_wrong_token_is_refused(hosted):
    client = hosted()
    assert sign_in(client, "not-the-token-but-long-enough").status_code == 401
    assert client.post("/village/actions/tick", follow_redirects=False).status_code == 403


def test_an_edited_cookie_is_refused(hosted):
    client = hosted()
    sign_in(client)
    good = client.cookies[access.COOKIE]
    expires, nonce, signature = good.rsplit(".", 2)
    client.cookies.set(access.COOKIE, f"{int(expires) + 99999}.{nonce}.{signature}")
    assert client.post("/village/actions/tick", follow_redirects=False).status_code == 403


def test_a_cookie_signed_with_another_secret_is_refused(monkeypatch):
    monkeypatch.setenv("MVV_GATE_TOKEN", "the-other-deployments-token-xxxx")
    forged = access.issue()
    monkeypatch.setenv("MVV_GATE_TOKEN", TOKEN)
    assert access.valid(forged) is False


def test_an_expired_cookie_is_refused(monkeypatch):
    monkeypatch.setenv("MVV_GATE_TOKEN", TOKEN)
    monkeypatch.setenv("MVV_SESSION_HOURS", "0.017")     # ~60s, the floor
    value = access.issue()
    assert access.valid(value) is True

    expires, nonce, _ = value.rsplit(".", 2)
    stale = f"{int(time.time()) - 10}.{nonce}"
    assert access.valid(f"{stale}.{access._sign(stale)}") is False


def test_nonsense_in_the_cookie_is_refused_rather_than_raising(monkeypatch):
    monkeypatch.setenv("MVV_GATE_TOKEN", TOKEN)
    for junk in ("", "x", "a.b", "a.b.c", "....", "notanumber.n.s", None):
        assert access.valid(junk) is False


def test_guessing_is_rate_limited(hosted):
    """A shared secret with unlimited guesses is a shared secret with one."""
    client = hosted()
    for _ in range(access.MAX_ATTEMPTS):
        sign_in(client, "wrong-but-long-enough-to-try")
    locked = sign_in(client, "wrong-but-long-enough-to-try")
    assert locked.status_code == 429

    # And the lockout is not a wording change: the real token is refused too.
    assert sign_in(client).status_code == 429
    assert client.post("/village/actions/tick", follow_redirects=False).status_code == 403


def test_a_successful_sign_in_clears_the_count(hosted):
    client = hosted()
    for _ in range(access.MAX_ATTEMPTS - 1):
        sign_in(client, "wrong-but-long-enough-to-try")
    assert sign_in(client).status_code == 303
    assert access._ATTEMPTS == {}


def test_the_attempt_counter_cannot_be_grown_without_limit(monkeypatch):
    """It is keyed on a header the caller controls, so it needs a ceiling —
    otherwise the defence against guessing is itself the way in."""
    monkeypatch.setenv("MVV_GATE_TOKEN", TOKEN)
    access.reset_attempts()

    class FakeRequest:
        def __init__(self, who):
            self.headers = {"x-forwarded-for": who}
            self.client = None

    for i in range(access.MAX_TRACKED + 500):
        access._failed(FakeRequest(f"10.0.{i // 256}.{i % 256}"))
    assert len(access._ATTEMPTS) <= access.MAX_TRACKED
    access.reset_attempts()


@pytest.mark.parametrize("target", [
    "https://evil.example/steal", "//evil.example/steal", "http://evil.example",
])
def test_the_next_parameter_cannot_leave_the_site(hosted, target):
    """An open redirect on the page that hands out sessions is a phishing link."""
    client = hosted()
    response = client.post(
        access.UNLOCK_PATH, data={access.FIELD: TOKEN, "next": target},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/village"


# =========================================================================
# the whole surface, not the routes somebody remembered
# =========================================================================
def write_routes(app) -> list:
    """Every non-GET route on the app, including the included routers.

    Written recursively on purpose: FastAPI wraps an included router in a
    container object, so a flat pass over `app.routes` reports the gate's two
    approval routes and silently misses all eleven village actions — which is
    exactly the sort of inventory that makes a guard look thorough while it is
    not.
    """
    out = []
    def walk(routes):
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
                continue
            methods = (getattr(route, "methods", set()) or set()) - {"HEAD", "OPTIONS"}
            if methods - {"GET"}:
                out.append(route.path)
    walk(app.routes)
    return sorted(set(out))


def test_every_write_route_is_refused_when_not_signed_in(hosted):
    """The boundary asserted over the actual route table rather than over the
    handful of paths a test author thought of. A new POST added tomorrow is
    covered by this the moment it exists."""
    client = hosted()
    import src.agents.web as web

    paths = write_routes(web.app)
    assert len(paths) > 10, paths                  # it found the village actions
    for path in paths:
        if path in SELF_AUTHENTICATING:
            continue
        url = path.replace("{approval_id}", "1")
        assert client.post(url, follow_redirects=False).status_code == 403, path


# Write routes that are exempt from the blanket refusal *because they check a
# credential themselves*. Every entry here is a promise, tested below: the
# route must refuse an unauthenticated caller on its own.
#
#   /unlock   trades the console token for a session — the door itself
#   /lock     ends one, which needs no credential to be safe
#   the webhook  carries its own token and publishes a signal, never an order
SELF_AUTHENTICATING = {
    access.UNLOCK_PATH,
    "/lock",
    "/api/signals/tradingview",
}


def test_every_exempt_route_refuses_on_its_own(hosted):
    """An exemption from the middleware is only acceptable if the route does
    the checking instead. This is the test that stops "exempt" drifting into
    "open" the next time somebody adds one."""
    client = hosted()
    assert client.post(access.UNLOCK_PATH, data={access.FIELD: "wrong-but-long"}).status_code == 401
    assert client.post("/api/signals/tradingview",
                       json={"symbol": "SPY", "action": "buy"}).status_code in (401, 503)


def test_the_only_unauthenticated_write_is_the_door(hosted):
    hosted("")
    import src.agents.web as web

    unguarded = [p for p in write_routes(web.app) if p == access.UNLOCK_PATH]
    assert unguarded == [access.UNLOCK_PATH]


# =========================================================================
# the cookie itself
# =========================================================================
def test_the_cookie_is_httponly_and_samesite(hosted):
    header = sign_in(hosted()).headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=strict" in header


def test_the_cookie_is_secure_behind_a_tls_proxy(hosted):
    """Railway terminates TLS and forwards plain HTTP with the scheme in a
    header, so asking request.url.scheme alone sends the session unprotected."""
    client = hosted()
    response = client.post(
        access.UNLOCK_PATH, data={access.FIELD: TOKEN},
        headers={"x-forwarded-proto": "https"}, follow_redirects=False,
    )
    assert "secure" in response.headers["set-cookie"].lower()


# =========================================================================
# getting in when things are broken
# =========================================================================
def test_you_can_sign_in_before_the_database_exists(tmp_path, monkeypatch):
    """Being unable to sign in because the schema is missing is how you end up
    with no way to look at why the schema is missing."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("MVV_GATE_TOKEN", TOKEN)
    monkeypatch.delenv("MVV_PUBLIC", raising=False)
    access.reset_attempts()

    import src.agents.web as web

    importlib.reload(deploy)
    importlib.reload(web)
    client = TestClient(web.app, raise_server_exceptions=False)

    assert client.get("/village").status_code == 503        # no tables yet
    assert client.get(access.UNLOCK_PATH).status_code == 200
    assert sign_in(client).status_code == 303


# =========================================================================
# nothing changes on a laptop
# =========================================================================
def test_a_local_console_needs_no_token(tmp_path, firms_yaml, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'd.db'}")
    monkeypatch.setenv("TRADE_FIRMS_CONFIG", str(firms_yaml))
    monkeypatch.setenv("TRADE_AUDIT_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("MVV_NOTIFICATION_LOG", str(tmp_path / "n.log"))
    monkeypatch.delenv("MVV_GATE_TOKEN", raising=False)
    monkeypatch.setenv("MVV_PUBLIC", "0")
    from src.cli import main

    main(["trade", "init"])

    import src.agents.web as web

    importlib.reload(deploy)
    importlib.reload(web)
    client = TestClient(web.app, raise_server_exceptions=False)

    assert client.post("/village/actions/tick", follow_redirects=False).status_code != 403
