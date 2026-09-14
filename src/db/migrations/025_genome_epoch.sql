-- Which measurement regime a stored genome score was produced under.
--
-- Migration 024 stored `fitted_bars` and `holdout_bars` beside every held-out
-- fitness so that "the stored numbers are safe to subtract". On 2026-09-14
-- that property turned out never to have held, for two reasons found the same
-- day, and both of them are invisible in the row:
--
--   * **The holdout overlapped the window it was held out from.** `_split`
--     computed the split as an index and passed it to the backtester as
--     `steps`, and the backtester counts steps from its 90-bar warmup. On the
--     live village the fitted window was [90,594) and the "holdout" [504,720):
--     90 of 216 held-out bars — 42% — were bars the candidate had already been
--     scored on. On a 180-bar feed the holdout was entirely inside the fit.
--
--   * **Fitness had no benchmark.** `return_pct - maxDD/2` scored a firm that
--     returned +8.05% while the universe it traded returned +53.91% as +3.04,
--     a positive result. It is excess over a hurdle now, and the same genome
--     scores -50.87.
--
-- So every row written before the fix measures a different quantity from every
-- row written after it, and nothing on the row says so. Subtracting across the
-- boundary gives a difference that is mostly the bug — exactly the failure
-- `comparable_with` refuses in memory and that 024's bar counts were meant to
-- prevent on disk.
--
-- `epoch` is that missing fact. 1 is everything measured before 2026-09-14; 2
-- is the fixed regime. `evolver.MEASUREMENT_EPOCH` is the single writer, and it
-- is bumped whenever a change makes new scores incomparable with old ones.
--
-- **A separate table, not a column on `genome_holdout`.** Same reasoning as
-- 024, 022 and 021: every migration re-runs on every `init-db`, SQLite has no
-- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, and a column added here would
-- never reach the databases already running — the insert would fail into
-- `_keep_holdout`'s swallowed exception and take the whole diagnostic row with
-- it, silently.
--
-- Rows already on disk get epoch 1 by the backfill below rather than by a
-- default, because a default would also silently claim epoch 1 for any future
-- row whose writer forgot to say — and "unmarked" must not quietly mean
-- "pre-fix".

CREATE TABLE IF NOT EXISTS genome_epoch (
    genome_id INTEGER PRIMARY KEY,
    epoch INTEGER NOT NULL,
    reason TEXT DEFAULT '',
    created_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
);

-- Backfill: everything already stored was measured under the leaking holdout
-- and the benchmark-free fitness. Idempotent — `ON CONFLICT DO NOTHING` on the
-- primary key, so re-running `init-db` neither duplicates nor relabels, and a
-- row that epoch 2 has already claimed is left alone.
INSERT INTO genome_epoch (genome_id, epoch, reason)
SELECT id, 1,
       'measured before 2026-09-14: holdout overlapped the fitted window, and fitness had no benchmark'
FROM strategy_genomes
ON CONFLICT (genome_id) DO NOTHING;
