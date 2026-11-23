from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from branchiv_org.db import Base
from branchiv_org.tracker.refusals import Escalation, RefusalEvent, decide_escalation


@dataclass
class Ev:
    turn_index: int
    refusal_category: str | None


def decide(events: list[Ev], turn: int) -> Escalation:
    return decide_escalation(events, turn, threshold=3, window_turns=10).action


def test_single_refusal_continues() -> None:
    assert decide([Ev(5, "cyber")], 5) is Escalation.CONTINUE


def test_two_mixed_categories_warn() -> None:
    assert decide([Ev(3, "cyber"), Ev(5, "bio")], 5) is Escalation.WARN


def test_threshold_ends_session() -> None:
    events = [Ev(2, None), Ev(4, "cyber"), Ev(6, "bio")]
    assert decide(events, 6) is Escalation.END_SESSION_AND_FLAG


def test_consecutive_same_category_ends_session() -> None:
    assert decide([Ev(3, "bio"), Ev(4, "bio")], 4) is Escalation.END_SESSION_AND_FLAG


def test_consecutive_uncategorized_only_warns() -> None:
    assert decide([Ev(3, None), Ev(4, None)], 4) is Escalation.WARN


def test_refusals_outside_window_do_not_count() -> None:
    events = [Ev(1, "cyber"), Ev(2, "cyber"), Ev(20, "bio")]
    assert decide(events, 20) is Escalation.CONTINUE


def test_threshold_below_two_rejected() -> None:
    with pytest.raises(ValueError):
        decide_escalation([], 0, threshold=1, window_turns=10)


def test_refusal_event_roundtrip_sqlite() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            RefusalEvent(
                user_id="u1",
                session_id="s1",
                turn_index=4,
                user_input="something refused",
                refusal_category="cyber",
                refusal_explanation=None,
                model="claude-opus-5-5",
                request_id="req_1",
            )
        )
        session.commit()
        row = session.scalars(select(RefusalEvent)).one()
    assert row.reviewed is False
    assert row.created_at is not None
    assert decide_escalation([row], 4, threshold=3, window_turns=10).action is Escalation.CONTINUE
