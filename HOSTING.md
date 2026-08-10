# Putting the village on the internet

There are two copies of this system and they do different jobs.

| | where | what it does |
|---|---|---|
| **The console** | your own machine | everything: approve, tick, recruit, kill |
| **The mirror** | a hosted URL | shows the village, changes nothing |

The split is not a limitation of the host. The approval gate has no
authentication — `POST /approvals/3/approve` with a form field of
`approved_by=web` grants a capital allocation to whoever sent it — so it does
not go on a public URL. The hosted copy refuses every write in middleware and
strips the controls out of the HTML, because a button that returns 403 when
clicked is worse than no button.

So: **host it to show people, run it locally to use it.**

---

## Hand this to Claude Code

If you would rather not do it by hand, paste this into Claude Code on your own
machine, with the repository open:

> I have a Vercel deployment of this repo with a Postgres database attached.
> The page says the database is empty. Create the schema and seed it, then
> start the local console.
>
> The connection string is already exported in my shell as `$DATABASE_URL` —
> read it from the environment, don't ask me to paste it and don't write it
> into any file.
>
> 1. Confirm `$DATABASE_URL` is set and starts with `postgres`.
> 2. `python -m src.main init-db`
> 3. `python -m src.main trade init`
> 4. `python -m src.main trade simulate --days 45`
> 5. Tell me to redeploy on Vercel, then open `/village` on the hosted URL.
> 6. Then start the local console against a separate local SQLite database, so
>    my clicking around doesn't write to the hosted one.

Before you paste it, put the connection string in your shell — see below.

---

## The connection string

Vercel → your project → **Storage** → the Postgres database → **`.env.local`**
tab. Copy the value of `POSTGRES_URL_NON_POOLING`.

Use the **non-pooling** one for this. Migrations through a connection pooler
are a known way to end up with a half-applied schema. The deployment itself
prefers the same variable, and Vercel sets both for you.

Then, in Terminal:

```bash
export DATABASE_URL='<paste it here>'
```

That is a password. Two habits worth keeping:

- **Don't paste it into a chat window** — not to me, not to anything else. Put
  it in the shell and refer to it as `$DATABASE_URL`.
- **Don't put it in a file in this repository.** `.env` is gitignored, but
  nothing in `src/` reads it automatically, so a `.env` here buys you the risk
  without the convenience. The shell export is what actually works.

The export lasts until you close that Terminal tab. That is a feature.

---

## Creating the schema

Three commands, from the repository root, in the tab where you exported it:

```bash
python -m src.main init-db                      # create the tables
python -m src.main trade init                   # create the firms from config
python -m src.main trade simulate --days 45     # give them a history to show
```

`init-db` is the one the hosted page asks for. The deployment will not run it
for you: it is read-only, and running schema changes from a public URL is not
read-only.

`trade init` never re-funds a firm that already exists, so it is safe to run
twice.

`simulate` is optional but worth it — without it every firm sits at
"Insufficient data", which is correct and also very boring to look at. 45 days
is enough for the first firm to clear the sample gate and earn a real score.

Then **redeploy on Vercel** and open **`/village`** on the hosted URL.

Note the path: `/` is the approval gate, which on a mirror is nearly empty by
design. `/village` is Mission Control. There is a link from one to the other.

---

## Running the real thing locally

The hosted copy can never approve anything. For the working console, use a
local SQLite file — and open a **new** Terminal tab, so the hosted
`DATABASE_URL` is not still exported and your clicking does not write to the
database the internet is reading:

```bash
python -m src.main init-db
python -m src.main trade init
python -m src.main serve
```

Then open **http://localhost:8000/village**.

It binds to `127.0.0.1` on purpose. Every button works, because the thing that
decides whether to spend money is on a machine you physically control.

---

## What the page is telling you

The mirror always says which of these it is, so you never have to guess.

| the page says | what happened | what to do |
|---|---|---|
| **The Village did not start**, with a traceback | the application could not be imported | read the traceback — it names the cause; a missing package is the usual one |
| **No database** | no `DATABASE_URL`, and the default SQLite file cannot be written on a serverless disk | attach Postgres and redeploy |
| **The database did not answer** | a database is configured but the connection failed | wrong host, wrong credentials, or not reachable from the deployment |
| **The database is empty** | it connects; the tables were never created | run `init-db` against it, from anywhere that can reach it |
| **This data does not update** | it is reading a SQLite file baked into the build | real data, frozen at upload time; attach Postgres for a village that moves |
| **Read-only mirror** | everything is working | this one is not a problem — it is the mirror saying what it is |

The first four are 503s with the reason in the page rather than a crash. That
took four rounds to get right; see `src/deploy.py` and `src/asgi.py` for why
each one exists.

---

## Environment variables

| variable | who sets it | what it does |
|---|---|---|
| `DATABASE_URL` | you | the database. `postgres://` and `postgresql://` both work |
| `POSTGRES_URL_NON_POOLING` | a managed add-on | preferred over the pooled URL |
| `POSTGRES_URL` | a managed add-on | used if the non-pooling one is absent |
| `MVV_PUBLIC` | you, rarely | forces read-only mode on or off |

Read-only mode turns itself on when the host looks like a serverless platform.
`MVV_PUBLIC=1` forces it on — useful for checking locally what a visitor will
see. `MVV_PUBLIC=0` forces it off, which you should not do on a public URL: it
puts the approval gate back on the internet with no authentication in front of
it.
