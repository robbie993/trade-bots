-- Keep the second exam, not just the first.
--
-- `strategy_genomes` has recorded `fitness` since it existed — the score a
-- genome got on the bars it was fitted to. The evolver also computes a
-- held-out score, on bars the genome was never chosen against, and then
-- throws it away: it lives on the in-memory Candidate, decides one adopt
-- or refuse, and is never written down.
--
-- That cost a day. "Does in-sample fitness predict held-out fitness" is the
-- one test that separates a strategy from a curve fit, and answering it
-- required re-running 280 backtests over 28 minutes to rebuild a number the
-- evolver had already computed and discarded. The answer, for the record,
-- was a Spearman rank correlation of +0.056 (n=280, t=+0.94): the selection
-- rule carries essentially no out-of-sample information. With this table the
-- test is a query, and the evolver reports every generation whether its own
-- ranking ranked anything.
--
-- **A separate table, not columns on `strategy_genomes`.** Every migration
-- here re-runs on every `init-db`, so each has to be idempotent, and SQLite
-- has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Same reasoning as 021
-- and 022, and it was nearly ignored here: the first draft of this file was
-- three bare ALTERs that would have thrown on the second run.
--
-- Both bar counts are stored because fitness is a window-sized quantity, not
-- a rate — max drawdown can only grow as a window lengthens, so two
-- fitnesses from different windows differ for reasons unrelated to the
-- genome. The same 40 random genomes scored -0.950 over 216 bars and -1.921
-- over 720, and that 0.972 gap was read as a real effect for an hour.
-- Storing the counts is what makes the stored numbers safe to subtract.

CREATE TABLE IF NOT EXISTS genome_holdout (
    genome_id INTEGER PRIMARY KEY,
    holdout_fitness NUMERIC,
    holdout_bars INTEGER DEFAULT 0,
    fitted_bars INTEGER DEFAULT 0,
    created_at TEXT DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
);
