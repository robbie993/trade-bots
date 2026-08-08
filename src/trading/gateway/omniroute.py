"""OmniRoute — the LLM gateway, and what happens when it is not there.

One OpenAI-compatible endpoint in front of many providers. The client below
speaks that protocol over the standard library, with retries and a hard
timeout.

The important part is what it is *allowed to do*. Nothing in this ecosystem
asks the gateway what to trade. Signals, sizing, risk, ethics and scoring are
all deterministic Python. The gateway writes **narration**: the paragraph a
human reads next to a decision that has already been made and stored.

So when it is disabled, unreachable, or slow, ``narrate`` returns a summary
assembled from the same inputs and the loop continues at full speed. That is
the whole design rule here — a trading system that stops deciding when an API
is down is not a trading system.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Sequence
from urllib import error, request

from ..config import GatewayConfig
from ..models import Signal, TradeProposal


@dataclass
class GatewayResult:
    text: str
    model: str
    source: str  # "gateway" | "offline"
    error: Optional[str] = None

    @property
    def is_live(self) -> bool:
        return self.source == "gateway"


class OmniRoute:
    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or GatewayConfig()

    # -- health -----------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.base_url)

    def health(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "reachable": False, "detail": "gateway disabled"}
        try:
            body = self._get(f"{self.config.base_url.rstrip('/')}/models")
            models = [m.get("id") for m in body.get("data", [])][:10]
            return {"enabled": True, "reachable": True, "models": models}
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return {"enabled": True, "reachable": False, "detail": str(exc)[:200]}

    # -- narration --------------------------------------------------------
    def narrate(self, proposal: TradeProposal, signals: Sequence[Signal] = ()) -> GatewayResult:
        """One paragraph explaining a decision that has already been made."""
        offline = self._offline_narration(proposal, signals)
        if not self.enabled:
            return GatewayResult(offline, "offline", "offline")

        prompt = (
            "Summarise this trading decision in three sentences for an audit log. "
            "Do not agree or disagree with it, do not offer advice, and do not "
            "introduce any number that is not given below.\n\n"
            f"{offline}"
        )
        try:
            text = self.complete(prompt)
            return GatewayResult(text, self.config.model, "gateway")
        except Exception as exc:  # noqa: BLE001 - narration never blocks a trade
            return GatewayResult(offline, "offline", "offline", error=str(exc)[:200])

    def complete(self, prompt: str, system: str = "") -> str:
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": (
                ([{"role": "system", "content": system}] if system else [])
                + [{"role": "user", "content": prompt}]
            ),
        }
        body = self._post(f"{self.config.base_url.rstrip('/')}/chat/completions", payload)
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("gateway returned no choices")
        return (choices[0].get("message") or {}).get("content", "").strip()

    # -- offline ----------------------------------------------------------
    def _offline_narration(
        self, proposal: TradeProposal, signals: Sequence[Signal] = ()
    ) -> str:
        lines = [
            f"{proposal.side.upper()} {proposal.quantity} {proposal.symbol} "
            f"at ~{proposal.reference_price} (notional {proposal.notional}).",
            f"Debate: {proposal.debate_winner or 'undecided'} won, confidence "
            f"{proposal.confidence}.",
            f"Rationale: {proposal.rationale or 'none recorded'}",
        ]
        for signal in signals:
            if isinstance(signal, Signal) and not signal.is_silent:
                lines.append(f"  {signal.analyst}: {signal.score} ({signal.confidence}) {signal.note}")
        if proposal.risk_verdict:
            lines.append(f"Risk: {proposal.risk_verdict} — {proposal.risk_reason}")
        if proposal.ethics_verdict:
            lines.append(f"Ethics: {proposal.ethics_verdict} — {proposal.ethics_reason}")
        return "\n".join(lines)

    # -- transport --------------------------------------------------------
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _post(self, url: str, payload: dict) -> dict:
        req = request.Request(url, data=json.dumps(payload).encode(), method="POST")
        for key, value in self._headers().items():
            req.add_header(key, value)
        return self._send(req)

    def _get(self, url: str) -> dict:
        req = request.Request(url, method="GET")
        for key, value in self._headers().items():
            req.add_header(key, value)
        return self._send(req)

    def _send(self, req) -> dict:
        try:
            with request.urlopen(req, timeout=self.config.timeout_s) as response:
                return json.loads(response.read().decode() or "{}")
        except error.HTTPError as exc:
            raise RuntimeError(f"gateway {exc.code}: {exc.read().decode()[:200]}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"gateway unreachable: {exc.reason}") from exc


__all__ = ["GatewayResult", "OmniRoute"]
