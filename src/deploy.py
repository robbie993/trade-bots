"""Running the village somewhere other than your laptop.

Every web page in this repository carries the same warning: *this page has no
authentication; bind it to localhost*. That is fine advice right up until you
put it on Vercel, at which point the approval gate — the one hard boundary in
front of every dollar — is a public URL where `POST /approvals/3/approve` with
a form field of `approved_by=web` grants a capital allocation to whoever sent
it.

So a hosted deployment runs in **public mode**: it can show you everything and
change nothing.

* every non-``GET`` request is refused, in middleware, so it covers the gate,
  the village actions and anything added later without anybody remembering to
  guard it;
* the buttons and forms are stripped from the HTML, because a control that
  returns 403 when clicked is worse than no control;
* the page says what it is, so nobody mistakes a mirror for the real console.

Public mode turns itself on when the host looks like a serverless platform, and
can be forced either way with ``MVV_PUBLIC``. It is not authentication — it is
the absence of a write surface, which is the part that can be verified by
reading the code.

**Storage.** Serverless filesystems are read-only apart from a per-instance
``/tmp`` that evaporates between requests, so the default SQLite file cannot
work: every tick, approval and fill would be written to a database that is
about to vanish, or fail outright. A hosted deployment needs ``DATABASE_URL``
pointing at a real Postgres. Rather than half-work, the page says so.

**The audit vault is off.** It writes Markdown to disk, which for the same
reason is either impossible or pointless here.

If you want the working console — the buttons, the approvals, the tick — run it
on your own machine. That is not a limitation of the deployment; it is where a
thing that approves spending belongs.
"""

from __future__ import annotations

import os
import re

# Set by the platform, not by us. Any of these means "you are not on a laptop".
HOSTED_MARKERS = ("VERCEL", "AWS_LAMBDA_FUNCTION_NAME", "FUNCTIONS_WORKER_RUNTIME",
                  "K_SERVICE", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME", "DYNO")

_FORM = re.compile(r"<form\b.*?</form>", re.S)
_BUTTON = re.compile(r"<button\b.*?</button>", re.S)
_FILE_INPUT = re.compile(r"<input\b[^>]*type=file[^>]*>", re.I)

BANNER = (
    "<div class='card alarm'><strong>Read-only mirror.</strong> "
    "This deployment can show the village and change nothing: every write is "
    "refused, and the controls have been removed rather than left to fail when "
    "clicked. The approval gate is the one boundary in front of every dollar "
    "and it does not belong on a public URL without authentication — so run "
    "<code>python -m src.main serve</code> on your own machine for the working "
    "console.</div>"
)

NO_DATABASE = (
    "<div class='card alarm'><strong>No database.</strong> "
    "This host has no writable disk, so the default SQLite file cannot be used: "
    "anything written would go to storage that disappears between requests. "
    "Attach a Postgres database and redeploy — <code>DATABASE_URL</code>, or "
    "the <code>POSTGRES_URL</code> a managed add-on sets for you.</div>"
)


def is_public() -> bool:
    """Whether this process should refuse to change anything."""
    forced = os.environ.get("MVV_PUBLIC", "").strip().lower()
    if forced in ("1", "true", "yes", "on"):
        return True
    if forced in ("0", "false", "no", "off"):
        return False
    return any(os.environ.get(marker) for marker in HOSTED_MARKERS)


def storage_is_durable() -> bool:
    """Postgres, or a SQLite file on a disk that will still be there.

    Asks Config rather than reading DATABASE_URL directly, so a database
    attached under a host's own name (POSTGRES_URL and friends) counts.
    """
    from .config import Config

    if Config().database_url.startswith(("postgres://", "postgresql://")):
        return True
    return not is_public()


def strip_controls(html: str) -> str:
    """Remove every control. Anchors survive — navigation is not a write."""
    html = _FORM.sub("", html)
    html = _BUTTON.sub("", html)
    html = _FILE_INPUT.sub("", html)
    return html


def announce(html: str) -> str:
    """Put the notice at the top of the page, wherever the page starts."""
    notice = BANNER if storage_is_durable() else NO_DATABASE + BANNER
    if "<body>" in html:
        return html.replace("<body>", "<body>" + notice, 1)
    return notice + html


def install(app) -> None:
    """Refuse writes in middleware, so nothing has to remember to guard itself.

    Deliberately a blanket rule on the HTTP method rather than a list of paths:
    a route added next month is covered without anybody thinking about it,
    which is the opposite of how the approval gate would otherwise erode.
    """
    if not is_public():
        return

    from fastapi import Request
    from fastapi.responses import HTMLResponse, PlainTextResponse

    @app.middleware("http")
    async def read_only(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            return PlainTextResponse(
                "This deployment is read-only.\n\n"
                "It can show the village and change nothing. The approval gate "
                "is the boundary in front of every dollar in this system, and "
                "it is not exposed on a public URL.\n\n"
                "Run `python -m src.main serve` on your own machine to use it.\n",
                status_code=403,
            )
        response = await call_next(request)
        if not response.headers.get("content-type", "").startswith("text/html"):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        return HTMLResponse(
            announce(strip_controls(body.decode())),
            status_code=response.status_code,
        )


__all__ = [
    "BANNER",
    "NO_DATABASE",
    "install",
    "is_public",
    "storage_is_durable",
    "strip_controls",
]
