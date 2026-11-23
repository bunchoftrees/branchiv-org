"""Refusal retention and the session escalation rule.

``refusal_events`` is the ONLY place raw user input is retained. It is kept for
safety review and possible legal hold, separate from general application logs, and
should get tighter access controls (and encryption at rest) than ordinary tables.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from branchiv_org.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RefusalEvent(Base):
    __tablename__ = "refusal_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    turn_index: Mapped[int] = mapped_column(Integer)
    user_input: Mapped[str] = mapped_column(Text)
    refusal_category: Mapped[str | None] = mapped_column(String(64))
    refusal_explanation: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(128))
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    review_note: Mapped[str | None] = mapped_column(Text)


class Escalation(StrEnum):
    CONTINUE = "continue"
    WARN = "warn"
    # Flag means "queue for human review", never an automatic report.
    END_SESSION_AND_FLAG = "end_session_and_flag"


class RefusalLike(Protocol):
    @property
    def turn_index(self) -> int: ...

    @property
    def refusal_category(self) -> str | None: ...


@dataclass(frozen=True)
class EscalationDecision:
    action: Escalation
    reason: str


def decide_escalation(
    events: Sequence[RefusalLike],
    current_turn: int,
    *,
    threshold: int,
    window_turns: int,
) -> EscalationDecision:
    """Decide what to do after a refusal, from one session's refusal events.

    Only refusals in the last ``window_turns`` turns (ending at ``current_turn``) count.
    - 0 or 1 refusals: CONTINUE. A single refusal never ends a session, because
      the safety classifiers have false positives.
    - ``threshold`` or more refusals: END_SESSION_AND_FLAG.
    - The two most recent refusals share a (non-null) category: END_SESSION_AND_FLAG.
    - Otherwise (2+ refusals, mixed categories): WARN.
    """
    if threshold < 2:
        raise ValueError("threshold must be at least 2")
    start = current_turn - window_turns
    recent = sorted(
        (e for e in events if start < e.turn_index <= current_turn),
        key=lambda e: e.turn_index,
    )
    count = len(recent)
    if count <= 1:
        return EscalationDecision(Escalation.CONTINUE, f"{count} refusal(s) in window")
    if count >= threshold:
        return EscalationDecision(
            Escalation.END_SESSION_AND_FLAG,
            f"{count} refusals in last {window_turns} turns",
        )
    last, prev = recent[-1].refusal_category, recent[-2].refusal_category
    if last is not None and last == prev:
        return EscalationDecision(
            Escalation.END_SESSION_AND_FLAG, f"consecutive refusals in category {last!r}"
        )
    return EscalationDecision(Escalation.WARN, f"{count} refusals in window")
