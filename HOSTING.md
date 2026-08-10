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

## Setting up the hosted database

One command, from the repository root:

```bash
./scripts/host_setup.sh
```

It asks for the connection string, checks the database answers before it
changes anything, creates the schema, creates the firms and gives them a
history to show. Then redeploy on Vercel and open **`/village`**.

Where to find the connection string: Vercel → your project → **Storage** → the
Postgres database → the **`.env.local`** tab → copy the value of
**`POSTGRES_URL_NON_POOLING`**.

Use the non-pooling one. Migrations sent through a connection pooler are a
known way to end up with a schema that is half applied. The deployment prefers
the same variable, and Vercel sets both for you.

**That string is a password.** The script reads it without echoing and does not
write it anywhere, so it stays out of your screen, your shell history and this
repository. Don't paste it into a chat window — not to a person, not to an
agent, not to me. If something already exported `DATABASE_URL`, the script uses
that and doesn't ask.

Note the path at the end: `/` is the approval gate, which on a mirror is nearly
empty by design. `/village` is Mission Control. There is a link between them.

### If you would rather hand it to an agent

Paste this into Claude Code on your own machine, with the repository open:

> Run `./scripts/host_setup.sh` in this repo. It will prompt me for a database
> connection string — that is a password, so let it prompt me directly, don't
> ask me to paste it to you and don't write it into any file. Tell me what to
> do next based on what it prints.

### What it does, if you would rather do it by hand

```bash
export DATABASE_URL='<the connection string>'
python -m src.main init-db                      # create the tables
python -m src.main trade init                   # create the firms from config
python -m src.main trade simulate --days 45     # give them a history to show
```

`init-db` is the one the hosted page asks for. The deployment will not run it
for you: it is read-only, and running schema changes from a public URL is not
read-only.

`trade init` never re-funds a firm that already exists, so it is safe to run
twice. `simulate` is optional but worth it — without it every firm sits at
"Insufficient data", which is correct and also very boring to look at. 45 days
is enough for the first firm to clear the sample gate and earn a real score.

---

## Running the real thing locally

The hosted copy can never approve anything. For the working console:

```bash
./scripts/local_console.sh
```

Then open **http://localhost:8000/village**. Every button works.

The first thing that script does is unset `DATABASE_URL`, and that is the
reason it exists. If you have just set up the hosted database, the connection
string is still exported in that terminal, and starting the console there would
send your clicking straight into the database the internet is reading. It uses
a local SQLite file instead, every time.

It binds to `127.0.0.1` on purpose. The thing that decides whether to spend
money runs on a machine you physically control.

Seed it with a history too, if you want something to look at immediately:

```bash
MVV_SEED_DAYS=45 ./scripts/local_console.sh
```

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
