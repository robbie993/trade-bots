"""Notification channels.

The operator only finds out about a kill trigger through these. A channel
that silently does not run is the alert not arriving.
"""

from __future__ import annotations

from src.notifications import (
    FileNotifier,
    MultiNotifier,
    NullNotifier,
    build_notifier,
)
from src.config import Config


class Failing:
    def send(self, subject, body):
        return False


class Exploding:
    def send(self, subject, body):
        raise RuntimeError("channel is on fire")


def test_every_channel_receives_the_message(tmp_path):
    """Regression: `any(...)` short-circuited, so a working console channel
    suppressed the durable file record entirely."""
    log = tmp_path / "notify.log"
    a, b = NullNotifier(), NullNotifier()
    multi = MultiNotifier([a, b, FileNotifier(log)])

    assert multi.send("subject", "body") is True
    assert len(a.sent) == 1
    assert len(b.sent) == 1
    assert "subject" in log.read_text()


def test_a_failing_channel_does_not_stop_the_others(tmp_path):
    log = tmp_path / "notify.log"
    recorder = NullNotifier()
    multi = MultiNotifier([Failing(), FileNotifier(log), recorder])

    assert multi.send("kill trigger", "details") is True
    assert "kill trigger" in log.read_text()
    assert recorder.sent


def test_send_reports_false_when_nothing_got_through():
    assert MultiNotifier([Failing(), Failing()]).send("s", "b") is False


def test_file_notifier_creates_the_directory(tmp_path):
    log = tmp_path / "nested" / "deep" / "notify.log"
    assert FileNotifier(log).send("subject", "body") is True
    assert log.exists()


def test_file_notifier_appends(tmp_path):
    log = tmp_path / "notify.log"
    notifier = FileNotifier(log)
    notifier.send("first", "a")
    notifier.send("second", "b")
    text = log.read_text()
    assert "first" in text and "second" in text


def test_build_notifier_always_includes_console_and_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MVV_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("MVV_SMTP_HOST", raising=False)
    notifier = build_notifier(Config(notification_log=tmp_path / "n.log"))
    kinds = {type(c).__name__ for c in notifier.channels}
    assert kinds == {"ConsoleNotifier", "FileNotifier"}


def test_slack_channel_is_added_when_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("MVV_SMTP_HOST", raising=False)
    notifier = build_notifier(
        Config(
            notification_log=tmp_path / "n.log",
            slack_webhook_url="https://hooks.slack.invalid/x",
        )
    )
    assert "SlackWebhookNotifier" in {type(c).__name__ for c in notifier.channels}


def test_the_gate_still_records_when_a_channel_explodes(db, tmp_path):
    """A Slack outage must not stop the ledger recording that a kill fired."""
    from src.agents.experiment_ledger import ExperimentLedger
    from src.agents.human_gate import HumanGate

    from .conftest import make_experiment

    ledger = ExperimentLedger(db)
    exp = ledger.create_experiment(make_experiment())
    gate = HumanGate(db, MultiNotifier([FileNotifier(tmp_path / "n.log")]))

    approval = gate.request_kill(exp, "CTR too low")
    assert approval.is_pending
    assert db.query_one("SELECT COUNT(*) AS n FROM human_approvals")["n"] == 1
