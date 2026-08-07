"""Notification channels for the human operator.

Every channel implements ``send(subject, body)`` and every channel is
best-effort: a Slack outage must never stop the ledger from recording that a
kill was triggered. Failures are printed and swallowed, and the file channel
is always on so there is a durable local record of what the operator was told.
"""

from __future__ import annotations

import json
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, Protocol

from .config import Config
from .db.connection import utcnow_iso


class Notifier(Protocol):
    def send(self, subject: str, body: str) -> bool: ...


@dataclass
class ConsoleNotifier:
    def send(self, subject: str, body: str) -> bool:
        print(f"\n=== {subject} ===\n{body}\n", flush=True)
        return True


@dataclass
class FileNotifier:
    path: Path

    def send(self, subject: str, body: str) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n--- {utcnow_iso()} | {subject}\n{body}\n")
            return True
        except OSError as exc:  # pragma: no cover - filesystem dependent
            print(f"[notify] file channel failed: {exc}")
            return False


@dataclass
class SlackWebhookNotifier:
    webhook_url: str
    timeout: int = 10

    def send(self, subject: str, body: str) -> bool:
        payload = json.dumps({"text": f"*{subject}*\n```{body}```"}).encode()
        request = urllib.request.Request(
            self.webhook_url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # pragma: no cover
            print(f"[notify] slack channel failed: {exc}")
            return False


@dataclass
class EmailNotifier:
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipient: str
    timeout: int = 20

    def send(self, subject: str, body: str) -> bool:  # pragma: no cover - needs SMTP
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.sender or self.user
        message["To"] = self.recipient
        message.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                smtp.starttls()
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.send_message(message)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            print(f"[notify] email channel failed: {exc}")
            return False


@dataclass
class MultiNotifier:
    channels: list

    def send(self, subject: str, body: str) -> bool:
        """Deliver to *every* channel, then report whether any got through.

        Not `any(...)`: that short-circuits on the first success, so the
        console channel would silently suppress the file, Slack and email
        channels — leaving no durable record of what the operator was told.
        Each channel already swallows its own failures.
        """
        results = [channel.send(subject, body) for channel in list(self.channels)]
        return any(results)


@dataclass
class NullNotifier:
    """Used by tests. Records instead of sending."""

    sent: list = None

    def __post_init__(self):
        if self.sent is None:
            self.sent = []

    def send(self, subject: str, body: str) -> bool:
        self.sent.append((subject, body))
        return True


def build_notifier(config: Optional[Config] = None) -> MultiNotifier:
    """Assemble the channels the environment has actually configured."""
    cfg = config or Config()
    channels: list = [ConsoleNotifier(), FileNotifier(cfg.notification_log)]
    if cfg.slack_webhook_url:
        channels.append(SlackWebhookNotifier(cfg.slack_webhook_url))
    if cfg.smtp_host and cfg.operator_email:
        channels.append(
            EmailNotifier(
                host=cfg.smtp_host,
                port=cfg.smtp_port,
                user=cfg.smtp_user,
                password=cfg.smtp_password,
                sender=cfg.smtp_from or cfg.smtp_user,
                recipient=cfg.operator_email,
            )
        )
    return MultiNotifier(channels)
