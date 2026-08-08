"""Watch an uploads directory and put each new file through the court.

This is the deployment prompt's `main_loop()` with its four defects fixed:

* it used ``Path`` without importing it — a NameError on the first iteration;
* it renamed into ``processed/`` without creating the directory;
* ``rename`` to a bare string silently clobbered any earlier file of the same
  name, destroying the previous evidence;
* a file that failed review was moved anyway, so the failure was unrecoverable.

Here a file only leaves ``uploads/`` once its case is on the docket, name
collisions are suffixed rather than overwritten, and a crash leaves the file
where it is so the next pass retries it.
"""

from __future__ import annotations

import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from ..db.connection import Database
from .file_court import FileCourt


class Watcher:
    def __init__(self, court: FileCourt, config, log=None):
        self.court = court
        self.config = config
        self._log_fn = log or self._default_log
        self._stop = False

    # -- logging ----------------------------------------------------------
    def _default_log(self, message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{stamp} {message}"
        print(line, flush=True)
        path = Path(self.config.log_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass  # a full disk must not take the court down

    def log(self, message: str) -> None:
        self._log_fn(message)

    # -- filesystem -------------------------------------------------------
    def ensure_dirs(self) -> None:
        for directory in (self.config.uploads_dir, self.config.processed_dir):
            Path(directory).mkdir(parents=True, exist_ok=True)

    def pending_files(self) -> list:
        uploads = Path(self.config.uploads_dir)
        if not uploads.exists():
            return []
        wanted = tuple(self.config.watch_extensions)
        return sorted(
            p
            for p in uploads.iterdir()
            if p.is_file() and p.suffix.lower() in wanted and not p.name.startswith(".")
        )

    def _destination(self, path: Path) -> Path:
        """A free path in processed/ — never overwrite prior evidence."""
        target = Path(self.config.processed_dir) / path.name
        if not target.exists():
            return target
        stem, suffix = path.stem, path.suffix
        for n in range(1, 10000):
            candidate = target.with_name(f"{stem}.{n}{suffix}")
            if not candidate.exists():
                return candidate
        raise OSError(f"cannot find a free name for {path.name} in processed/")

    # -- the loop ---------------------------------------------------------
    def process_once(self) -> list:
        """One sweep of uploads/. Returns the cases opened."""
        self.ensure_dirs()
        cases = []
        for path in self.pending_files():
            self.log(f"court: processing {path.name}")
            try:
                case = self.court.process_file(path)
            except Exception as exc:
                # Leave the file in uploads/ so the next pass retries it.
                self.log(f"court: FAILED {path.name}: {type(exc).__name__}: {exc}")
                continue

            score = case.risk_score if case.risk_score is not None else "n/a"
            self.log(f"court: {path.name} -> {case.verdict} (risk {score})")
            if case.pending_reviewers:
                self.log(
                    f"court: {path.name} still needs "
                    f"{', '.join(case.pending_reviewers)}"
                )

            try:
                path.rename(self._destination(path))
            except OSError as exc:
                self.log(f"court: could not archive {path.name}: {exc}")
            cases.append(case)
        return cases

    def stop(self, *_args) -> None:
        self._stop = True

    def run_forever(self) -> None:  # pragma: no cover - long-running
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        self.ensure_dirs()
        self.log("court: watching " + str(self.config.uploads_dir))
        while not self._stop:
            self.process_once()
            for _ in range(self.config.watch_interval_s):
                if self._stop:
                    break
                time.sleep(1)
        self.log("court: stopped")


def build_watcher(config) -> Watcher:
    db = Database.from_url(config.database_url)
    db.init_schema()
    return Watcher(FileCourt(db, config.court), config.court)
