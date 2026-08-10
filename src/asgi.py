"""The deployment entrypoint — and a fallback that cannot itself fail.

A serverless platform reports *any* failure to load the application as one
opaque message: `FUNCTION_INVOCATION_FAILED`, with the real cause in a log you
have to go and find. Three rounds were spent guessing at one of those, and the
third guess was wrong for the same reason the first two were: the code meant to
explain the problem could only run if the application had already loaded.

So this module loads the real app inside a `try`, and when that fails it serves
the traceback instead.

**The fallback imports nothing.** Not FastAPI, not Starlette, not anything in
this repository — it is a bare ASGI callable written against the standard
library. That is the whole point: the most likely reason an application fails
to load on a new platform is that its dependencies are not installed, and a
fallback that imports FastAPI to report "FastAPI is missing" reports nothing at
all.

It catches ``BaseException`` rather than ``Exception``, because a missing C
extension can surface as something that is not an ``Exception``, and an
entrypoint whose safety net has holes in exactly the interesting cases is not
worth having.

Point the platform here:

    [tool.vercel]
    entrypoint = "src.asgi:app"

Locally nothing needs this — ``uvicorn src.agents.web:app`` is unchanged, and
when the import succeeds this module *is* that app, one attribute lookup away.
"""

from __future__ import annotations

import traceback

_ERROR = ""
_APP = None

try:
    from .agents.web import app as _APP  # noqa: F401
except BaseException:  # noqa: BLE001 - reporting this is the entire job
    _ERROR = traceback.format_exc()


_PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<title>The Village did not start</title><style>
body{{font:15px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;max-width:56rem;
margin:2.5rem auto;padding:0 1.5rem;color:#111;background:#fff}}
@media(prefers-color-scheme:dark){{body{{color:#e7e7e7;background:#111}}}}
h1{{font-family:system-ui,sans-serif;font-size:1.3rem}}
p{{font-family:system-ui,sans-serif}}
pre{{white-space:pre-wrap;border:1px solid #8884;border-left:4px solid #b91c1c;
border-radius:8px;padding:1rem;font-size:.82rem;overflow-x:auto}}
</style></head><body>
<h1>The Village did not start</h1>
<p>The application could not be imported, so none of it is running. This is the
reason, verbatim:</p>
<pre>{trace}</pre>
{hint}
<p>Nothing has been damaged: this process never reached the database, and the
approval gate grants nothing when it is not running.</p>
</body></html>"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


# The failure that actually happens on a fresh platform, named rather than
# left as a traceback to interpret.
MISSING_DEPS = (
    "<p><strong>This is a dependency that was never installed</strong>, not a "
    "bug in the village. The host built the application without installing "
    "what it imports. Check that the build read the <code>dependencies</code> "
    "in <code>pyproject.toml</code> \u2014 an empty list there is read as "
    "\u201cnothing to install\u201d \u2014 or point the build at "
    "<code>requirements.txt</code>, which lists the full stack.</p>"
)


def _hint(trace: str) -> str:
    if "ModuleNotFoundError" not in trace:
        return ""
    for package in ("fastapi", "starlette", "pydantic", "multipart", "uvicorn"):
        if f"'{package}" in trace:
            return MISSING_DEPS
    return ""


async def _report(scope, receive, send) -> None:
    """A bare ASGI app. No imports, so it works when nothing else does."""
    if scope["type"] == "lifespan":
        # Answer the startup/shutdown handshake or the server may hang.
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    if scope["type"] != "http":
        return

    path = scope.get("path", "/")
    wants_json = path.startswith("/api/")
    if wants_json:
        body = (
            '{"ok": false, "error": "the application failed to import", '
            f'"traceback": {_json_string(_ERROR)}}}'
        ).encode()
        content_type = b"application/json"
    else:
        body = _PAGE.format(trace=_escape(_ERROR), hint=_hint(_ERROR)).encode()
        content_type = b"text/html; charset=utf-8"

    await send({
        "type": "http.response.start",
        "status": 500,
        "headers": [
            (b"content-type", content_type),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def _json_string(text: str) -> str:
    """json.dumps for one string, without importing json.

    Overcautious on purpose: this path exists for the case where imports are
    the thing that is broken, and `json` is stdlib but the habit is the point.
    """
    out = ['"']
    for char in text:
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


# The real application when it loaded, the explanation when it did not.
app = _APP if _APP is not None else _report

__all__ = ["app"]
