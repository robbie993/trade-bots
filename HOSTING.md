# Putting the village on the internet

There are three ways to run this, and the difference between them is *what may
change things*.

| | where | what it does |
|---|---|---|
| **The console** | your own machine | everything: approve, tick, recruit, kill |
| **The mirror** | a hosted URL, no token | shows the village, changes nothing |
| **The hosted village** | a hosted URL, `MVV_GATE_TOKEN` set | ticks on its own; the console works once you sign in |

The reason the mirror exists: the approval gate has no authentication of its
own — `POST /approvals/3/approve` with a form field of `approved_by=web` grants
a capital allocation to whoever sent it. On a public URL with nothing in front
of it, that is the whole system's one boundary, open. So the default hosted
deployment refuses every write in middleware and strips the controls out of the
HTML, because a button that returns 403 when clicked is worse than no button.

**`MVV_GATE_TOKEN` is what changes that**, and nothing else does. Set it and
`/unlock` will trade the token for a signed, expiring, HttpOnly session cookie;
writes work for that session and no other. Leave it unset and you have the
mirror, exactly as before — there is no half-open state, because the code asks
one question (`access.unlocked(request)`) and a deployment with no token
configured can only ever answer no. See `src/access.py`.

What signing in does *not* do is widen what the buttons may do. The council
still may never decide live trading, the live venues still refuse to send an
order until the venue itself is approved, and the risk manager, the conscience
and the reconciliation are untouched. It restores the controls; it does not
change the rules underneath them.

---

## Railway: the whole village, running without you

Railway runs containers rather than functions, so unlike a serverless host it
can run the part that actually matters — **the tick loop**. That is the
difference between a hosted picture of a village and a hosted village.

Two services from this one repository, sharing one database:

| service | `MVV_ROLE` | what it is |
|---|---|---|
| **worker** | `worker` | migrates, creates the firms, then ticks forever. No HTTP surface at all |
| **web** | `web` | serves Mission Control and the gate. Never migrates anything |

The split is a safety boundary, not a deployment detail. The worker trades,
scores, kills, holds the council and runs the living quarters — and none of it
is reachable from the internet, because it does not listen on a port. The web
service is the only thing exposed, and it is read-only until somebody signs in.

### Setting it up, the short way

```bash
./scripts/railway_setup.sh --dry-run     # read what it will do
./scripts/railway_setup.sh               # then do it
```

It creates both services, adds Postgres, wires the variables, generates a
sign-in token and deploys — the same clicks below, in the right order. It runs
on **your** machine because Railway's CLI authenticates as you, with an
account-level credential that can change every project in the workspace; that
belongs in your keychain via `railway login` and nowhere else.

The token it generates goes to Railway through stdin rather than as a command
argument — arguments are visible to every process via `ps` and land in your
shell history — and is saved to `.env`, which is gitignored and written mode
600. It is deliberately never printed. When you need it:

```bash
grep MVV_GATE_TOKEN .env
```

Nothing it does is destructive: creating a service that exists is skipped,
setting a variable to what it already is changes nothing, and it deletes
nothing. Run it twice and the second run is a no-op with a fresh deploy.

### Setting it up, by hand

1. **Add Postgres.** In your project: *New → Database → PostgreSQL*.

2. **Create the worker service** from this repository. In its *Variables*:

   ```
   MVV_ROLE=worker
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   MVV_TICK_INTERVAL=300
   ```

   Railway's `${{Postgres.DATABASE_URL}}` is a reference — type it exactly like
   that and it resolves to the real connection string without either of you
   ever seeing it. It needs **no** public domain; it does not serve anything.

3. **Create the web service** from the same repository. In its *Variables*:

   ```
   MVV_ROLE=web
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   MVV_GATE_TOKEN=<a long random string>
   ```

   Generate the token — do not invent one by hand:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Then *Settings → Networking → Generate Domain*.

4. **Open `https://<your-domain>/village`.** It will show the village. Click
   **Sign in**, paste the token, and the controls come back.

That is it. The worker is already ticking, and it keeps ticking whether or not
anybody is signed in or looking — that is the point of it being a separate
service.

### What you will see while it starts

The web service usually boots before the worker has finished migrating, so the
first page says **"The database is empty"** with the command to fix it. Ignore
it and reload in a few seconds: the worker creates the tables, and the web
service notices on its own. It re-checks every few seconds rather than
remembering the answer, precisely so this resolves without a redeploy.

### The token, honestly

It is one shared secret, not user accounts, because there is one of you. It is
signed with HMAC, expires (12 hours by default, `MVV_SESSION_HOURS`), is
`HttpOnly` and `SameSite=Strict`, is marked `Secure` behind Railway's TLS
proxy, and wrong guesses are rate-limited to 8 before a 15-minute lockout. A
token under 16 characters is **refused as a configuration** — the deployment
stays a mirror and says why, because a short password in front of an approval
gate is worse than an honest mirror.

Put it in Railway's variables and nowhere else. Not in the repository, not in a
chat window, not in a file you commit. If it leaks, change the variable and
redeploy — every existing session is signed with the old one and dies with it.

### What is not persistent

The audit vault writes Markdown to the container's disk, which Railway replaces
on every deploy. The ledger, the fills, the approvals and the lessons are all
in Postgres and survive; the vault is a rendering of them and does not. If you
want it, run `trade audit --write` locally against the same database.

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

## Leaving it running

`local_console.sh` serves pages and nothing else. The autoplay button in
Mission Control ticks the village from the browser, so closing the tab stops
it — the only thing ticking was a page.

To leave the village running on its own:

```bash
./scripts/village.sh start      # the tick loop and the console, in the background
./scripts/village.sh status     # what is alive, when it last ticked, what waits on you
./scripts/village.sh logs       # follow it
./scripts/village.sh stop
```

That starts two processes. The village keeps ticking while you look at Mission
Control, look at the solar view, look at nothing, or shut the laptop. Both
write to the same SQLite file, which is why the connection now opens in WAL
mode — the default journal makes a reader and a writer block each other and
fail with "database is locked".

It turns on two things, deliberately:

| | |
|---|---|
| `TRADE_AUTONOMY=council` | the council rules on what the evidence settles and defers the rest to you. It has no panel for live trading, by construction |
| `TRADE_LIVING=on` | the arena, bazaar and tavern run themselves |

**Neither grants a dollar.** Capital still stops at the gate, which is what
makes this safe to leave running — and why you will still come back to
decisions waiting for you.

### What the village does on its own

With `TRADE_LIVING=on`, three quarters of the map that used to be scenery
start doing things:

| quarter | what happens | how often |
|---|---|---|
| **Arena** | a season of head-to-head bouts, and milestone awards | every 10 bars |
| **Bazaar** | an idle firm lists the genome it is not using; someone with tokens to spare buys it | every 4 bars |
| **Tavern** | firms with overlapping universes form an alliance; someone behind spies on someone ahead | every 3 bars |

Two limits, both deliberate:

- **The bazaar trades in tokens only.** Capital listings still exist and still
  stop at the gate before a dollar moves — but the village will not file those
  requests on its own, because a gate that fills up while you sleep becomes an
  inbox to clear rather than a decision to make.
- **The tavern cannot reach the money.** It is handed a read-only store and a
  writer restricted to two tables, enforced in `sandbox/guard.py`. Espionage
  copies a genome into a record; using it still means submitting it to the
  strategy court like anything else.

The clock is the market's bar date, not the wall clock, so replaying the same
history produces the same village — the same alliance on the same day. Tune it
with `TRADE_SEASON_EVERY`, `TRADE_BAZAAR_EVERY`, `TRADE_TAVERN_EVERY`, or turn
the whole thing off by leaving `TRADE_LIVING` unset.

### The switches, without a terminal

Mission Control has a **Switches** panel. It can pause the whole village, and
open or close each quarter, while everything is running:

| switch | what it stops |
|---|---|
| **The village** | trading, scoring, the council — the loop keeps running and does nothing |
| **Arena** | seasons and titles |
| **Bazaar** | listings and sales |
| **Tavern** | alliances and schemes |

These are not environment variables. The process that ticks is not the process
serving the page, so setting an environment variable in one would change
nothing in the other. They are stored in `village_settings` and read on every
pass, which means a switch you flip is already in effect — no restart, and no
Terminal once the village is up.

An unset switch defers to `TRADE_LIVING`, so the environment still decides the
default and clearing an override hands the decision back to it. Every flip
records who made it.

Pausing stops the village trading. It does not close the gate, and nothing
already approved is undone.

### Bringing a firm back

A paused firm now has a **Bring back** button next to it in Mission Control.
That was the one decision you could not make from the page you were looking
at — `trade resume` could do it, and so could the council, but Mission Control
could only watch the firm sit there.

**A killed firm does not come back**, and the button does not appear for one.
A pause is the system saying "this tripped a limit, go and look"; a kill is the
answer to having looked. Reversing that from a web button would make the kill
switch a suggestion. The legitimate route is the one that any strategy takes:
submit it to the court, and if it clears, it becomes a new firm — created
paused and unfunded, with an approval waiting for you.

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
| `MVV_GATE_TOKEN` | you | the sign-in token. Unset means the mirror; 16+ characters means `/unlock` works |
| `MVV_SESSION_HOURS` | you, rarely | how long a sign-in lasts. Default 12 |
| `MVV_ROLE` | you | `web` (serve pages) or `worker` (tick the village). Container only |
| `MVV_TICK_INTERVAL` | you | seconds between ticks in the worker. Default 300 |
| `PORT` | the platform | what the web service listens on. Railway sets it |

Read-only mode turns itself on when the host looks like a hosted platform.
`MVV_PUBLIC=1` forces it on — useful for checking locally what a visitor will
see. `MVV_PUBLIC=0` forces it off, and you should not do that on a public URL:
it puts the approval gate back on the internet with nothing in front of it.
`MVV_GATE_TOKEN` is the supported way to get the controls back, and unlike
`MVV_PUBLIC=0` it asks who you are first.
