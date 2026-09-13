"""Financial-event normalization and cash-impact classification helpers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping


class UnresolvedAmountError(ValueError):
    """Raised when a cash-impacting event has no resolved amount."""


@dataclass(frozen=True)
class NormalizedCashFlow:
    """An event with its cash date and signed cash impact."""

    event_id: str
    user_id: str
    event_type: str
    category: str
    direction: str
    amount: Decimal | None
    currency: str
    event_date: date
    settlement_date: date | None
    status: str
    linked_event_id: str | None
    flexibility: str
    minimum_allowed_amount: Decimal | None
    cash_date: date | None
    signed_cash_impact: Decimal | None


# Pending/scheduled debits represent obligations for forecasting. Credits are
# included only after settlement, avoiding unconfirmed cash inflows.
_CASH_IMPACTING_STATUSES = {"settled", "pending", "scheduled"}
_CASH_IMPACTING_DEBIT_STATUSES = {"settled", "pending", "scheduled"}
_CASH_IMPACTING_CREDIT_STATUSES = {"settled"}


def get_events_for_user(
    events: Iterable[Mapping[str, Any]], user_id: str
) -> list[Mapping[str, Any]]:
    """Return source events belonging to ``user_id``."""

    return [event for event in events if event.get("user_id") == user_id]


def is_unresolved_event(event: Mapping[str, Any]) -> bool:
    """Return whether an event amount is missing and needs image resolution."""

    return event.get("amount") is None


def unresolved_events(events: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return events whose amounts are still unresolved."""

    return [event for event in events if is_unresolved_event(event)]


def is_cash_impacting(event: Mapping[str, Any]) -> bool:
    """Return whether an event should affect a forecasted cash balance."""

    status = event.get("status")
    direction = event.get("direction")
    if event.get("settlement_date") is None:
        return False
    if direction == "debit":
        return status in _CASH_IMPACTING_DEBIT_STATUSES
    if direction == "credit":
        return status in _CASH_IMPACTING_CREDIT_STATUSES
    return False


def signed_cash_impact(event: Mapping[str, Any]) -> Decimal:
    """Return the signed cash impact, or zero for non-cash-impacting events."""

    if not is_cash_impacting(event):
        return Decimal("0")
    amount = event.get("amount")
    if amount is None:
        raise UnresolvedAmountError(
            f"Cash-impacting event has unresolved amount: {event.get('event_id')}"
        )
    if event.get("direction") == "debit":
        return -amount
    return amount


def normalize_event(event: Mapping[str, Any]) -> NormalizedCashFlow:
    """Convert one source event into a normalized cash-flow record."""

    cash_date = event.get("settlement_date") if is_cash_impacting(event) else None
    impact = signed_cash_impact(event) if cash_date is not None else None
    return NormalizedCashFlow(
        event_id=event["event_id"],
        user_id=event["user_id"],
        event_type=event["event_type"],
        category=event["category"],
        direction=event["direction"],
        amount=event.get("amount"),
        currency=event["currency"],
        event_date=event["event_date"],
        settlement_date=event.get("settlement_date"),
        status=event["status"],
        linked_event_id=event.get("linked_event_id"),
        flexibility=event["flexibility"],
        minimum_allowed_amount=event.get("minimum_allowed_amount"),
        cash_date=cash_date,
        signed_cash_impact=impact,
    )


def normalize_events(events: Iterable[Mapping[str, Any]]) -> list[NormalizedCashFlow]:
    """Convert source events into normalized cash-flow records."""

    return [normalize_event(event) for event in events]


def group_events_by_settlement_date(
    events: Iterable[Mapping[str, Any]],
) -> dict[date, list[NormalizedCashFlow]]:
    """Group cash-impacting normalized events by settlement date."""

    grouped: dict[date, list[NormalizedCashFlow]] = defaultdict(list)
    for flow in normalize_events(events):
        if flow.cash_date is not None:
            grouped[flow.cash_date].append(flow)
    return dict(grouped)
