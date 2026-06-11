"""Stage 6 — hourly lifecycle checks on open tickets.

Exit on: target | stop | time-stop | narrative fade (velocity sign-flip while
in profit -> take it) | invalidator hit. Every exit records exit_reason.

Phase 0 runs this read-only (tickets managed by hand); Phase 1 automates the
exits. The decision logic is identical either way, so it lives here from day
one.
"""

from __future__ import annotations

from datetime import datetime

from ..types import ExitReason, FeedState, Ticket


def unrealised_pnl(ticket: Ticket, price: float) -> float:
    return ticket.direction * (price - ticket.entry_price) * ticket.size


def check_exit(
    ticket: Ticket,
    price: float,
    now: datetime,
    feed_states: dict[str, FeedState] | None = None,
    feed_weights: dict[str, float] | None = None,
    invalidator_hit: bool = False,
) -> ExitReason | None:
    """Return the exit reason if the ticket should close now, else None.

    Checks are ordered by precedence: an explicit invalidator outranks
    everything; protective stop outranks target on the same bar (conservative
    paper-fill assumption); narrative fade only fires in profit.

    invalidator_hit is supplied by the caller — invalidators are feed-spec
    conditions confirmed upstream (Phase 0: by hand; later: by a checker).
    """
    if invalidator_hit:
        return ExitReason.INVALIDATOR

    if ticket.direction > 0:
        if price <= ticket.stop_price:
            return ExitReason.STOP
        if price >= ticket.target_q75:
            return ExitReason.TARGET
    else:
        if price >= ticket.stop_price:
            return ExitReason.STOP
        if price <= ticket.target_q75:
            return ExitReason.TARGET

    if now >= ticket.time_stop_at:
        return ExitReason.TIME_STOP

    # Narrative fade: the story's velocity has flipped against the position
    # while we're in profit -> take it. feed_weights are the *signed*
    # instrument weights of the ticket's trigger feeds, so net pressure is in
    # price-direction terms.
    if feed_states and feed_weights and unrealised_pnl(ticket, price) > 0:
        net_velocity = sum(
            w * feed_states[f].direction * abs(feed_states[f].velocity)
            for f, w in feed_weights.items()
            if f in feed_states
        )
        if net_velocity * ticket.direction < 0:
            return ExitReason.NARRATIVE_FADE

    return None
