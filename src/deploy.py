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
from typing import Optional

# Set by the platform, not by us. Any of these means "you are not on a laptop".
# Any of these means "you are not on a laptop". More than one per platform on
# purpose: VERCEL is reliable at build time and VERCEL_URL/VERCEL_ENV at invoke
# time, and getting this wrong used to disable the guard entirely.
HOSTED_MARKERS = (
    "VERCEL", "VERCEL_URL", "VERCEL_ENV", "VERCEL_REGION",
    "AWS_LAMBDA_FUNCTION_NAME", "LAMBDA_TASK_ROOT",
    "FUNCTIONS_WORKER_RUNTIME", "K_SERVICE", "RAILWAY_ENVIRONMENT",
    "FLY_APP_NAME", "DYNO",
)

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


UNREACHABLE = (
    "<div class='card alarm'><strong>The database did not answer.</strong> "
    "A database is configured but the connection failed — wrong host, wrong "
    "credentials, or it is not reachable from this deployment.</div>"
)


NO_SCHEMA = (
    "<div class='card alarm'><strong>The database is empty.</strong> "
    "It connects, but the tables have never been created — a freshly attached "
    "database is empty by definition. Point the migrations at it once, from "
    "anywhere that can reach it:<br>"
    "<code>DATABASE_URL=&lt;your url&gt; python -m src.main init-db</code><br>"
    "This deployment will not do it for you: it is read-only, and running "
    "schema changes from a public URL is not read-only.</div>"
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
    """Whether the configured URL points at storage that outlives a request.

    A judgement about the *URL*, used to word the message: SQLite on a
    serverless host is not durable however writable it looks, because the disk
    is gone by the next invocation.
    """
    from .config import Config

    if Config().database_url.startswith(("postgres://", "postgresql://")):
        return True
    return not is_public()


# "ok" | "unreachable" | "empty". Cached: a serverless invocation is short,
# and probing per request costs more than it tells you.
_STATE: Optional[str] = None

# Enough of the schema to know `init-db` has been run. Not the full list —
# this is a liveness check, not a migration checker.
CORE_TABLES = ("experiments", "human_approvals", "cash_flow")


def database_state(force: bool = False) -> str:
    """Whether the database can be opened, and whether it has been migrated.

    Two failures, not one, and they need different instructions. The first
    version only checked that a connection opened — which an empty database
    does perfectly well, so the page then crashed on the first real query with
    `no such table: firms`. A freshly attached Postgres is empty by
    definition, so that was the state every new deployment would land in.
    """
    global _STATE
    if _STATE is not None and not force:
        return _STATE
    try:
        from .config import Config
        from .db.connection import Database

        db = Database.from_url(Config().database_url)
        try:
            tables = set(db.table_names())
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - any failure to connect is one answer
        _STATE = "unreachable"
        return _STATE
    _STATE = "ok" if set(CORE_TABLES) <= tables else "empty"
    return _STATE


def database_ok(force: bool = False) -> bool:
    return database_state(force) == "ok"


def unavailable_notice() -> str:
    """Why there is nothing to show, in the terms that actually apply."""
    if database_state() == "empty":
        return NO_SCHEMA
    if not storage_is_durable():
        return NO_DATABASE
    return UNREACHABLE


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


def _explain(request, html: str, status: int, detail: str):
    """The same answer in whichever dialect the caller asked in."""
    from fastapi.responses import HTMLResponse, JSONResponse

    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": detail, "read_only": True},
                            status_code=status)
    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<title>The Village</title>"
        "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:44rem;"
        "margin:3rem auto;padding:0 1.5rem;color:#111;background:#fff}"
        "@media(prefers-color-scheme:dark){body{color:#e7e7e7;background:#111}}"
        ".card{border:1px solid #8884;border-left:4px solid #b91c1c;"
        "border-radius:8px;padding:1rem;margin:.75rem 0}"
        "pre{white-space:pre-wrap;font-size:.85rem}</style></head><body>"
        f"<h1>The Village</h1>{html}</body></html>",
        status_code=status,
    )


def install(app) -> None:
    """Guard every request. Installed unconditionally, on purpose.

    The first version returned early unless ``is_public()`` — which made every
    protection here depend on correctly guessing the platform from environment
    variables. When the guess was wrong the middleware was simply absent and
    the handler crashed exactly as it had before, which is the failure this
    module exists to prevent.

    So the split is by *what*, not by *where*:

    * refusing writes and stripping controls are about being public, and stay
      gated on ``is_public()``;
    * a database that cannot be used is a known condition with a clear answer,
      and gets that answer everywhere;
    * an unexpected exception is only swallowed when public. Locally a
      traceback belongs in your terminal, not behind a friendly page.
    """
    from fastapi import Request
    from fastapi.responses import PlainTextResponse

    @app.middleware("http")
    async def guard(request: Request, call_next):
        public = is_public()

        if public and request.method not in ("GET", "HEAD", "OPTIONS"):
            return PlainTextResponse(
                "This deployment is read-only.\n\n"
                "It can show the village and change nothing. The approval gate "
                "is the boundary in front of every dollar in this system, and "
                "it is not exposed on a public URL.\n\n"
                "Run `python -m src.main serve` on your own machine to use it.\n",
                status_code=403,
            )

        # Answered before the handler runs. Every page opens the database to
        # render, so on a host with no writable disk the handler raises while
        # connecting — and a message rendered afterwards never gets written,
        # because there is no response to write it into.
        if not database_ok():
            return _explain(request, unavailable_notice() + (BANNER if public else ""),
                            503, f"the database is {database_state()}")

        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            if not public:
                raise
            return _explain(
                request,
                "<div class='card alarm'><strong>The village could not be "
                f"read.</strong><pre>{type(exc).__name__}: {exc}</pre></div>"
                + BANNER,
                500,
                f"{type(exc).__name__}: {exc}",
            )

        if not public:
            return response
        if not response.headers.get("content-type", "").startswith("text/html"):
            return response
        from fastapi.responses import HTMLResponse

        body = b"".join([chunk async for chunk in response.body_iterator])
        return HTMLResponse(
            announce(strip_controls(body.decode())),
            status_code=response.status_code,
        )


__all__ = [
    "BANNER",
    "NO_DATABASE",
    "NO_SCHEMA",
    "UNREACHABLE",
    "database_ok",
    "database_state",
    "unavailable_notice",
    "install",
    "is_public",
    "storage_is_durable",
    "strip_controls",
]
