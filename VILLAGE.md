# The AI Village — deployment notes

This is the corrected companion to the "AI Village v7.0" deployment prompt.
Every link and package below was probed before being written down; where the
original prompt was wrong, this says so and gives the working version.

The short version: **the File Court is built and running in this repo. The
reviewers it calls are third-party tools you install on your own machine.**

---

## What was wrong in the original prompt

Five defects, all of which would have bitten on the first run.

| Phase | Problem | Status |
|-------|---------|--------|
| 2.1 | CodeTribunal is on **Hugging Face**, not GitHub. `git clone https://github.com/amine-yagoub/CodeTribunal.git` 404s. | Fixed — correct URL below |
| 4.2 | `Experiment.kill_if_necessary()` reads `self.chargeback_rate`, which is never defined → `AttributeError` | Not applied; this repo's `Experiment` is already correct |
| 4.2 | Money as `float` | Not applied; this repo uses `Decimal` deliberately |
| 4.3 | `run_codetribunal()` returns `{"risk_score": 50}` when the subprocess prints nothing | Fixed — see "The silent-MIXED bug" |
| 4.4 | `main_loop()` uses `Path` with no `from pathlib import Path` → `NameError`; also renames into `processed/` without creating it, clobbering same-named files | Fixed in `src/court/watcher.py` |
| 3 | The migration recreates `experiments`, `orders`, `cash_flow`, `human_approvals` — tables 001–005 already own, with **more** columns | Not applied; only the new `file_cases` table was added, as `006` |

### The silent-MIXED bug

This is the one worth understanding, because it makes a broken system look
like a working one.

```python
# from the original prompt
result = subprocess.run(cmd, capture_output=True, text=True)
return json.loads(result.stdout) if result.stdout else {"risk_score": 50}
```

CodeTribunal isn't installed → no stdout → risk 50 → `aggregate_verdicts`
sees 40 < 50 < 70 → **MIXED**. Every file, forever. The dashboard fills with
verdicts, the docket fills with rows, and nothing has been reviewed.

The rule here instead: **a reviewer that did not run does not get a score.**
A backend that can't run returns `available=False` and the reason. If no
backend scored the file the verdict is `UNREVIEWED` — a fourth band the
original spec didn't have. `UNREVIEWED` is not `NOT_GUILTY`; an unread file
has not been cleared.

```
$ mvv court review suspect.py
suspect.py: UNREVIEWED  (risk n/a)
  No reviewer produced a score (codetribunal).
  [SKIP] codetribunal: CodeTribunal not cloned at /home/user/trade-bots/CodeTribunal
```

---

## Verified component locations

Probed on 2026-08-08.

| Tool | Where it really is | Install |
|------|--------------------|---------|
| CodeTribunal | **huggingface.co**/amine-yagoub/CodeTribunal | `git clone https://huggingface.co/amine-yagoub/CodeTribunal` |
| Tribunal skill | github.com/hekman316/claude-skill-tribunal | `SKILL.md` → `~/.claude/skills/tribunal/` |
| Agent Review Panel | github.com/wan-huiyan/agent-review-panel | `/plugin install roundtable@agent-review-panel` |
| Yama | github.com/**juspay**/yama | `npm install -g @juspay/yama` (v3.0.4) |
| Ruflo | npm `ruflo` (v3.34.0), github.com/ruvnet/ruflo | `/plugin install ruflo-core@ruflo` |
| Lattice | npm `lattice-agents` (v0.2.2) | `npx lattice-agents ./` |
| Swarm / ClaudeVille / RufloUI / OpenClaw | miopea/swarm, deadronos/claude-ville, Mario-PB/rufloui, bokiko/openClaw-dashboard | all resolve; pick one |

Note the Yama org: the prompt's prose said `juspay/yama` in one place and the
npm package is `@juspay/yama`. Both are right; there is no `wan-huiyan/yama`.

---

## What runs where

Two very different halves, and conflating them is why the original prompt
reads as if one session could do all of it.

**In this repo (done, committed, tested):**

- `src/court/` — the court, backends, and watcher
- `src/db/migrations/006_create_file_cases.sql` — the docket, plus a SQLite mirror
- `mvv court …` CLI
- 32 new tests

**On your machine (you must run these — they are per-user installs):**

- `/plugin` commands — these mutate your local Claude Code config
- The `tribunal` skill in `~/.claude/skills/`
- CodeTribunal's clone and `pip install -r requirements.txt`
- Any dashboard — they serve on `localhost`, so they must run where your browser is
- Obsidian, `brew services start postgresql`

A remote container can't do the second column for you: nothing it installs
reaches your laptop, and `localhost:3000` there is not `localhost:3000` here.

---

## Getting the court running

```bash
# 1. Schema (adds file_cases; leaves 001-005 alone)
python -m src.main init-db

# 2. What can actually review a file right now?
python -m src.main court doctor

# 3. Install the one reviewer that runs headless
git clone https://huggingface.co/amine-yagoub/CodeTribunal
cd CodeTribunal && pip install -r requirements.txt && cp .env.example .env
#   set ZAI_API_KEY and ZAI_API_BASE in that .env
cd ..

# 4. Confirm it registered
python -m src.main court doctor        # expect: [OK ] tier1 codetribunal: ready

# 5. Review something
python -m src.main court review path/to/file.py

# 6. Or watch a directory
mkdir -p uploads processed
python -m src.main court watch          # --once for a single sweep
```

### Escalation

Reviewers are tiered so a clean file never pays for the expensive ones.

```
tier 1  CodeTribunal          every file
tier 2  /tribunal             only if tier 1 scored > 30
tier 3  /roundtable panel     only if the running mean is > 50
```

Verdict bands: `> 70` GUILTY, `> 40` MIXED, else NOT_GUILTY, and `UNREVIEWED`
when nothing scored it. All five thresholds are `Decimal` and configurable —
see `CourtConfig` in `src/config.py`.

### The two interactive reviewers

`/tribunal` and `/roundtable:agent-review-panel` are prompts that run inside a
Claude Code conversation. There is no headless entrypoint to shell out to, so
the court does **not** pretend to invoke them. It records the obligation:

```bash
python -m src.main court pending
```

...then you run the slash command against the files it lists. Recording the
debt is honest; inventing a score would not be.

---

## Configuration

All environment-overridable, all in `src/config.py`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `MVV_CODETRIBUNAL_PATH` | `./CodeTribunal` | where the clone lives |
| `MVV_UPLOADS_DIR` | `./uploads` | watched directory |
| `MVV_PROCESSED_DIR` | `./processed` | archive after review |
| `MVV_COURT_LOG` | `./logs/village.log` | watcher log |
| `MVV_COURT_EXTENSIONS` | `.zip,.py,.js,.md` | what to pick up |
| `MVV_COURT_INTERVAL_S` | `60` | sweep interval |
| `MVV_COURT_TIMEOUT_S` | `600` | per-reviewer timeout |
| `MVV_COURT_TRIBUNAL_ABOVE` | `30` | escalate to tier 2 |
| `MVV_COURT_PANEL_ABOVE` | `50` | escalate to tier 3 |
| `MVV_COURT_MIXED_ABOVE` | `40` | MIXED band |
| `MVV_COURT_GUILTY_ABOVE` | `70` | GUILTY band |
| `MVV_COURT_YAMA` | `false` | add Yama to tier 1 |

---

## A design note

`README.md` §2.1 lists the court system as **deliberately not built** — one of
the things Phase 1 excluded to stay killable. This adds it, so that line is
now out of date on purpose.

Worth keeping in view: the court reviews *files*, and the Experiment Ledger
decides *spending*. They share a database and nothing else. No court verdict
touches an experiment, a budget, or an approval, and the human gate in front
of every dollar is exactly where it was. If the court is ever wired into
spending decisions, that is the moment to re-read §6.
