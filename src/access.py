"""Signing in, so the village can be used from somewhere other than your desk.

Everything hosted in this repository has so far been a **mirror**: it shows the
village and changes nothing, because the approval gate — the one boundary in
front of every dollar — has no authentication, and `POST /approvals/3/approve`
with a form field of `approved_by=web` grants a capital allocation to whoever
sent it. Refusing every write was the honest answer to that, and it is still
the default.

It is also, on a host you actually run the village from, the wrong answer. A
village ticking on Railway with a console you cannot use is a village you have
to open a laptop and a database tunnel to operate. So there is now a second
answer: **prove you are you, and the write surface comes back**.

    no token configured   →  read-only mirror. Exactly as before.
    token configured      →  /unlock signs you in; writes work for that session.

**Fail closed, always.** Not setting ``MVV_GATE_TOKEN`` is not a degraded mode
that leaves a door ajar — it is the mirror, unchanged, with no way in at all.
Every branch here answers "no" when it is not certain of "yes":

* a token shorter than ``MIN_TOKEN`` is refused *as a configuration*, so a
  four-character password cannot be what stands between the internet and your
  ledger. The page says so rather than pretending to be locked;
* the cookie is HMAC-signed with the token and carries its own expiry, so it
  cannot be extended, edited or replayed past its life;
* comparisons are ``hmac.compare_digest``, so a wrong guess takes the same
  time as a right one;
* failed attempts are rate-limited per client, because a shared secret with
  unlimited guesses is a shared secret with one guess;
* the cookie is ``HttpOnly``, ``SameSite=Strict``, and ``Secure`` whenever the
  request arrived over TLS.

**What this is not.** It is one shared secret, not user accounts — there is one
of you. It records *that* a session was signed in, and the approval rows still
record ``approved_by``, which remains whatever the form says. If this ever grows
past one operator, that is the thing to fix first.

**What it deliberately does not unlock.** Nothing here touches the rules below
it. The council still may never decide live trading, the live venues still
refuse to send an order until a human approves the venue itself, and the risk
manager, the conscience and the reconciliation are exactly as they were. Signing
in restores the buttons; it does not widen what the buttons may do.
"""

# No `from __future__ import annotations` here, deliberately. FastAPI resolves
# a handler's annotations against the *module* namespace, and the routes below
# are defined inside `install()` with `Request` and `Form` imported locally — so
# under postponed evaluation the annotation is the string "Request", resolves to
# nothing, and FastAPI decides `request` must be a query parameter. Every sign-in
# then fails with a 422 about a missing field. Those local imports are what let
# this module load without fastapi installed, which the CLI relies on, so the
# future import is the part that goes.

import hmac
import os
import secrets
import time
from hashlib import sha256
from typing import Optional

# The cookie, the route, and the form field. Named once so the middleware
# exemption and the form cannot drift apart.
COOKIE = "mvv_session"
UNLOCK_PATH = "/unlock"
FIELD = "token"

# Shorter than this is not a secret, it is a speed bump. A hosted deployment
# with a six-character token would report itself as protected while being
# guessable over a weekend, so it is refused as a configuration instead.
MIN_TOKEN = 16

# How long a sign-in lasts. Long enough not to be a nuisance on a phone, short
# enough that a forgotten open tab is not a standing grant.
DEFAULT_HOURS = 12

# A shared secret with unlimited guesses is a shared secret with one guess.
MAX_ATTEMPTS = 8
LOCKOUT_SECONDS = 900

# client -> [failures, locked_until]. In process, which is the right scope: it
# is one container, and a rate limiter that needs a database to say "no" is a
# rate limiter that fails open when the database is the thing under load.
_ATTEMPTS: dict = {}


# =========================================================================
# configuration
# =========================================================================
def token() -> str:
    """The configured shared secret, or "" when there is none."""
    return os.environ.get("MVV_GATE_TOKEN", "").strip()


def session_seconds() -> int:
    try:
        hours = float(os.environ.get("MVV_SESSION_HOURS", DEFAULT_HOURS))
    except ValueError:
        hours = DEFAULT_HOURS
    return max(60, int(hours * 3600))


def configured() -> bool:
    """Whether signing in is possible at all. False means the mirror stands."""
    return len(token()) >= MIN_TOKEN


def misconfigured() -> bool:
    """A token was set, and is too short to be one.

    Worth distinguishing from "no token": one of these is a decision and the
    other is a mistake, and only the mistake needs telling.
    """
    return 0 < len(token()) < MIN_TOKEN


# =========================================================================
# the cookie
# =========================================================================
def _sign(payload: str) -> str:
    return hmac.new(token().encode(), payload.encode(), sha256).hexdigest()


def issue() -> str:
    """A fresh session cookie value: expiry, nonce, and a signature over both.

    The expiry travels inside the signed payload rather than relying on the
    cookie's own ``Max-Age``, which is a request from the browser and not a
    fact about the session. A client that keeps the cookie past its life
    presents something this refuses.
    """
    expires = int(time.time()) + session_seconds()
    payload = f"{expires}.{secrets.token_urlsafe(12)}"
    return f"{payload}.{_sign(payload)}"


def valid(value: Optional[str]) -> bool:
    """Whether a cookie was signed by this token and has not expired."""
    if not value or not configured():
        return False
    try:
        expires_raw, nonce, signature = str(value).rsplit(".", 2)
    except ValueError:
        return False
    if not hmac.compare_digest(_sign(f"{expires_raw}.{nonce}"), signature):
        return False
    try:
        return int(expires_raw) > time.time()
    except ValueError:
        return False


def unlocked(request) -> bool:
    """Whether this request may change things. The whole question, in one call."""
    if not configured():
        return False
    return valid(request.cookies.get(COOKIE))


# =========================================================================
# guessing
# =========================================================================
def _who(request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or "unknown"


def locked_out(request) -> int:
    """Seconds left on a lockout, or 0."""
    entry = _ATTEMPTS.get(_who(request))
    if not entry:
        return 0
    remaining = int(entry[1] - time.time())
    return remaining if remaining > 0 else 0


# A counter keyed on something the caller controls grows as fast as the caller
# likes. Spoofed X-Forwarded-For values would otherwise let a few million
# requests turn a rate limiter into a memory exhaustion bug — the defence
# against guessing becoming the way in.
MAX_TRACKED = 4096


def _forget_stale() -> None:
    now = time.time()
    for who, entry in list(_ATTEMPTS.items()):
        if entry[1] < now:                   # not currently locked out
            _ATTEMPTS.pop(who, None)
    if len(_ATTEMPTS) > MAX_TRACKED:         # still too many: all of them are locked
        _ATTEMPTS.clear()                    # start over rather than grow


def _failed(request) -> None:
    if len(_ATTEMPTS) >= MAX_TRACKED:
        _forget_stale()
    who = _who(request)
    entry = _ATTEMPTS.setdefault(who, [0, 0.0])
    entry[0] += 1
    if entry[0] >= MAX_ATTEMPTS:
        entry[1] = time.time() + LOCKOUT_SECONDS
        entry[0] = 0


def _succeeded(request) -> None:
    _ATTEMPTS.pop(_who(request), None)


def reset_attempts() -> None:
    """For tests, and for a restart. Not reachable over HTTP."""
    _ATTEMPTS.clear()


# =========================================================================
# the pages
# =========================================================================
def _secure(request) -> bool:
    """Whether to mark the cookie Secure.

    Behind Railway's proxy the request reaches the app as plain HTTP with the
    real scheme in a header, so asking ``request.url.scheme`` alone would send
    a session cookie without the Secure flag on an HTTPS site.
    """
    if (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip() == "https":
        return True
    return request.url.scheme == "https"


STYLE = (
    "body{font:15px/1.6 ui-sans-serif,system-ui,-apple-system,sans-serif;"
    "max-width:30rem;margin:4rem auto;padding:0 1.5rem;color:#111;background:#fff}"
    "@media(prefers-color-scheme:dark){body{color:#e7e7e7;background:#111}}"
    "input{font:inherit;padding:.6rem;width:100%;border-radius:6px;"
    "border:1px solid #8886;background:transparent;color:inherit}"
    "button{font:inherit;padding:.6rem 1.2rem;margin-top:.75rem;border-radius:6px;"
    "border:1px solid #8886;background:transparent;color:inherit;cursor:pointer}"
    ".muted{color:#8a8a8a;font-size:.875rem}"
    ".bad{color:#b91c1c;font-weight:600}"
)


def _page(body: str, status: int = 200):
    from fastapi.responses import HTMLResponse

    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>The Village</title><style>{STYLE}</style></head><body>{body}"
        "</body></html>",
        status_code=status,
    )


def install(app) -> None:
    """Add ``/unlock``. Safe to call whether or not a token is configured.

    The routes exist either way so that the page can *explain* the mirror
    rather than 404 at somebody trying to work out how to get in.
    """
    from fastapi import Form, Request
    from fastapi.responses import RedirectResponse

    @app.get(UNLOCK_PATH)
    def unlock_form(request: Request, next: str = "/village"):
        if unlocked(request):
            return RedirectResponse(_safe_next(next), status_code=303)
        if misconfigured():
            return _page(
                "<h1>The Village</h1>"
                "<p class=bad>MVV_GATE_TOKEN is set, and is too short to be a "
                f"password.</p><p>It needs at least {MIN_TOKEN} characters. Until "
                "it does, this deployment stays a read-only mirror — a short "
                "secret in front of an approval gate is worse than an honest "
                "mirror, because it looks like protection.</p>"
                "<p class=muted>Generate one with "
                "<code>python -c \"import secrets;print(secrets.token_urlsafe(32))\"</code>"
                "</p>",
                status=503,
            )
        if not configured():
            return _page(
                "<h1>The Village</h1>"
                "<p>This deployment is a <strong>read-only mirror</strong>. It "
                "shows the village and changes nothing, because the approval "
                "gate does not belong on a public URL without a password in "
                "front of it.</p>"
                "<p>To use it from here, set <code>MVV_GATE_TOKEN</code> in the "
                "service's variables and redeploy. Then this page will ask for "
                "it.</p>"
                "<p class=muted>Generate one with "
                "<code>python -c \"import secrets;print(secrets.token_urlsafe(32))\"</code>"
                "</p>",
                status=503,
            )

        wait = locked_out(request)
        if wait:
            return _page(
                "<h1>The Village</h1>"
                f"<p class=bad>Too many wrong attempts. Try again in "
                f"{wait // 60 + 1} minute(s).</p>",
                status=429,
            )
        return _page(
            "<h1>The Village</h1>"
            "<p>Sign in to use the console. Without this the deployment shows "
            "the village and changes nothing.</p>"
            f"<form method=post action='{UNLOCK_PATH}'>"
            f"<input type=hidden name=next value='{_escape(_safe_next(next))}'>"
            f"<input type=password name='{FIELD}' autocomplete=current-password "
            "autofocus placeholder='access token'>"
            "<button>Sign in</button></form>"
            "<p class=muted>The village keeps ticking whether or not anybody is "
            "signed in. This decides who may change things, not whether it "
            "runs.</p>"
        )

    @app.post(UNLOCK_PATH)
    def unlock(request: Request, token_value: str = Form("", alias=FIELD),
               next: str = Form("/village")):
        if not configured():
            return _page("<h1>The Village</h1><p>No token is configured.</p>", status=503)
        if locked_out(request):
            return _page(
                "<h1>The Village</h1><p class=bad>Too many wrong attempts.</p>",
                status=429,
            )
        if not hmac.compare_digest(token_value.strip(), token()):
            _failed(request)
            return _page(
                "<h1>The Village</h1><p class=bad>That is not the token.</p>"
                f"<p><a href='{UNLOCK_PATH}'>Try again</a></p>",
                status=401,
            )

        _succeeded(request)
        response = RedirectResponse(_safe_next(next), status_code=303)
        response.set_cookie(
            COOKIE, issue(),
            max_age=session_seconds(),
            httponly=True,
            samesite="strict",
            secure=_secure(request),
            path="/",
        )
        return response

    @app.get("/lock")
    def lock(request: Request):
        response = RedirectResponse("/village", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response


def _safe_next(value: str) -> str:
    """Only ever redirect within this site.

    A `next` parameter that accepts absolute URLs is an open redirect, and an
    open redirect on the page that hands out sessions is how a sign-in link
    becomes a phishing link.
    """
    value = str(value or "/village")
    if not value.startswith("/") or value.startswith("//"):
        return "/village"
    return value


def _escape(value: str) -> str:
    import html

    return html.escape(str(value), quote=True)


def banner(request) -> str:
    """The line the mirror shows about how to stop being one."""
    if misconfigured():
        return (
            "<div class='card alarm'><strong>MVV_GATE_TOKEN is too short.</strong> "
            f"It needs at least {MIN_TOKEN} characters. This deployment is staying "
            "read-only until it does.</div>"
        )
    if configured():
        return (
            "<div class='card alarm'><strong>Read-only until you sign in.</strong> "
            f"<a href='{UNLOCK_PATH}'>Sign in</a> to use the controls. The village "
            "keeps ticking either way — this decides who may change things.</div>"
        )
    return ""


__all__ = [
    "COOKIE", "FIELD", "MAX_ATTEMPTS", "MIN_TOKEN", "UNLOCK_PATH", "banner",
    "configured", "install", "issue", "locked_out", "misconfigured",
    "reset_attempts", "session_seconds", "token", "unlocked", "valid",
]
