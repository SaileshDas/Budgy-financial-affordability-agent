"""Deterministic baseline forecasting, recurrence, amendments, and scenarios."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from code.currency import ConvertedCashFlow, convert_cash_flow_to_home_currency
from code.events import NormalizedCashFlow, UnresolvedAmountError, normalize_event
from code.image_evidence import resolve_image_amount
from code.loaders import LoadedData
from code.messages import MessageFact, ParsedMessage, parse_messages


@dataclass(frozen=True)
class RecurringTemplate:
    """A recurrence supported by repeated settled historical observations."""

    template_id: str
    user_id: str
    event_type: str
    category: str
    direction: str
    currency: str
    amount: Decimal
    flexibility: str
    minimum_allowed_amount: Decimal | None
    interval_days: int
    event_ids: tuple[str, ...]
    last_date: date


@dataclass(frozen=True)
class SpendingChange:
    """A validated change to one recurring expense template."""

    action: str
    event_id: str
    amount: Decimal | None = None


@dataclass(frozen=True)
class ForecastDay:
    """One calendar day's balance movement in the user's home currency."""

    date: date
    opening_balance: Decimal
    cash_inflows: Decimal
    cash_outflows: Decimal
    closing_balance: Decimal
    minimum_required_balance: Decimal
    cash_flows: tuple[ConvertedCashFlow, ...]


@dataclass(frozen=True)
class ForecastResult:
    """A baseline or scenario daily forecast for one user and request date."""

    user_id: str
    request_date: date
    end_date: date
    home_currency: str
    starting_balance: Decimal
    minimum_required_balance: Decimal
    days: tuple[ForecastDay, ...]
    recurring_templates: tuple[RecurringTemplate, ...] = ()
    applied_message_amendments: tuple[MessageFact, ...] = ()
    applied_spending_changes: tuple[SpendingChange, ...] = ()
    excluded_records: tuple[str, ...] = ()
    baseline_forecast: "ForecastResult | None" = None
    scenario_forecast: "ForecastResult | None" = None

    @property
    def minimum_projected_balance(self) -> Decimal:
        return min(day.closing_balance for day in self.days)

    @property
    def available_surplus(self) -> Decimal:
        return max(Decimal("0"), self.minimum_projected_balance - self.minimum_required_balance)


def _profile_for_user(loaded_data: LoadedData, user_id: str) -> dict:
    matches = [row for row in loaded_data["financial_profiles.csv"] if row.get("user_id") == user_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one financial profile for user: {user_id}")
    return matches[0]


def _forecast_end(request_date: date, desired_completion_date: date | None) -> date:
    return max(request_date + timedelta(days=90), desired_completion_date or request_date)


def _event_eligible_for_recurrence(event: Mapping[str, object]) -> bool:
    return (
        event.get("status") == "settled"
        and event.get("amount") is not None
        and event.get("settlement_date") is not None
        and event.get("direction") in {"debit", "credit"}
        and event.get("event_type") not in {"refund", "investment_sale", "investment_purchase", "investment_valuation"}
        and event.get("category") not in {"transfer", "investment"}
    )


def infer_recurring_templates(
    loaded_data: LoadedData, user_id: str, before_date: date | None = None
) -> tuple[RecurringTemplate, ...]:
    """Infer only repeated, stable, regularly spaced settled events."""

    groups: dict[tuple[object, ...], list[Mapping[str, object]]] = defaultdict(list)
    for event in loaded_data["financial_events.csv"]:
        settlement = event.get("settlement_date")
        if event.get("user_id") != user_id or not _event_eligible_for_recurrence(event):
            continue
        if before_date is not None and not isinstance(settlement, date):
            continue
        if before_date is not None and settlement >= before_date:
            continue
        key = (
            event.get("event_type"), event.get("category"), event.get("direction"),
            event.get("currency"), event.get("amount"), event.get("flexibility"),
            event.get("minimum_allowed_amount"),
        )
        groups[key].append(event)

    templates: list[RecurringTemplate] = []
    for events in groups.values():
        ordered = sorted(events, key=lambda item: item["settlement_date"])
        if len(ordered) < 3:
            continue
        dates = [item["settlement_date"] for item in ordered]
        gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
        if not gaps:
            continue
        if all(6 <= gap <= 8 for gap in gaps):
            interval = 7
        elif all(13 <= gap <= 16 for gap in gaps):
            interval = 14
        elif all(25 <= gap <= 35 for gap in gaps):
            interval = 30
        else:
            continue
        first = ordered[0]
        templates.append(
            RecurringTemplate(
                template_id=str(first["event_id"]),
                user_id=user_id,
                event_type=str(first["event_type"]),
                category=str(first["category"]),
                direction=str(first["direction"]),
                currency=str(first["currency"]),
                amount=first["amount"],
                flexibility=str(first["flexibility"]),
                minimum_allowed_amount=first.get("minimum_allowed_amount"),
                interval_days=interval,
                event_ids=tuple(str(item["event_id"]) for item in ordered),
                last_date=dates[-1],
            )
        )
    return tuple(templates)


def _profile_categories(profile: Mapping[str, object], field: str) -> set[str]:
    value = profile.get(field) or ""
    return {part for part in str(value).split("|") if part}


def _parse_spending_changes(
    changes: Iterable[str | SpendingChange],
    templates: tuple[RecurringTemplate, ...],
    profile: Mapping[str, object],
) -> tuple[SpendingChange, ...]:
    by_id = {template.template_id: template for template in templates}
    parsed: list[SpendingChange] = []
    seen: set[str] = set()
    for raw in changes:
        if isinstance(raw, SpendingChange):
            change = raw
        else:
            parts = str(raw).split(":")
            if len(parts) == 2 and parts[0] == "stop":
                change = SpendingChange("stop", parts[1])
            elif len(parts) == 3 and parts[0] == "reduce_to":
                try:
                    change = SpendingChange("reduce_to", parts[1], Decimal(parts[2]))
                except (InvalidOperation, ValueError) as exc:
                    raise ValueError(f"Invalid spending change: {raw}") from exc
            else:
                raise ValueError(f"Invalid spending change: {raw}")
        if change.event_id in seen or change.event_id not in by_id:
            raise ValueError(f"Unknown or duplicate recurring event: {change.event_id}")
        template = by_id[change.event_id]
        if template.direction != "debit":
            raise ValueError("Only recurring expenses can be changed")
        if change.action == "stop":
            if template.flexibility not in {"stoppable", "reducible_or_stoppable"}:
                raise ValueError(f"Event is not stoppable: {change.event_id}")
            if template.category not in _profile_categories(profile, "expense_categories_user_is_willing_to_stop"):
                raise ValueError(f"Category is not permitted to stop: {template.category}")
        elif change.action == "reduce_to":
            if change.amount is None or change.amount < 0:
                raise ValueError("Reduction amount must be a non-negative Decimal")
            if template.flexibility not in {"reducible", "reducible_or_stoppable"}:
                raise ValueError(f"Event is not reducible: {change.event_id}")
            if template.category not in _profile_categories(profile, "expense_categories_user_is_willing_to_reduce"):
                raise ValueError(f"Category is not permitted to reduce: {template.category}")
            if template.minimum_allowed_amount is not None and change.amount < template.minimum_allowed_amount:
                raise ValueError(f"Reduction is below minimum allowed amount: {change.event_id}")
            if change.amount > template.amount:
                raise ValueError("Reduction cannot exceed the original amount")
        else:
            raise ValueError(f"Unknown spending change action: {change.action}")
        seen.add(change.event_id)
        parsed.append(change)
    return tuple(parsed)


def _message_date(message: ParsedMessage) -> date | None:
    if isinstance(message.sent_at, datetime):
        return message.sent_at.date()
    return None


def _apply_message_facts(
    loaded_data: LoadedData,
    user_id: str,
    templates: tuple[RecurringTemplate, ...],
    messages: tuple[ParsedMessage, ...],
) -> tuple[tuple[RecurringTemplate, ...], tuple[MessageFact, ...], list[NormalizedCashFlow]]:
    """Apply only explicit facts that can safely amend existing records."""

    changed = list(templates)
    applied: list[MessageFact] = []
    synthetic: list[NormalizedCashFlow] = []
    by_event = {event["event_id"]: event for event in loaded_data["financial_events.csv"]}
    for parsed in messages:
        for fact in parsed.facts:
            if fact.status in {"pending", "open", "unrealized"} or not fact.spendable and fact.kind != "employment_ended":
                if fact.kind != "employment_ended":
                    continue
            if fact.kind == "salary_income" and fact.amount is not None and fact.currency:
                matched = [
                    template for template in changed
                    if template.category == "salary" and template.direction == "credit"
                ]
                if not matched:
                    continue
                effective = fact.effective_date
                for index, template in enumerate(changed):
                    if template in matched and effective is not None:
                        changed[index] = replace(
                            template,
                            amount=fact.amount,
                            currency=fact.currency,
                            last_date=max(
                                template.last_date,
                                effective - timedelta(days=template.interval_days),
                            ),
                        )
                applied.append(fact)
            elif fact.kind == "employment_ended":
                matched = [template for template in changed if template.category == "salary" and template.direction == "credit"]
                if matched and fact.effective_date is not None:
                    changed = [template for template in changed if template not in matched]
                    applied.append(fact)
            elif fact.kind == "refund" and fact.status == "completed" and fact.event_id in by_event:
                source = by_event[fact.event_id]
                amount = fact.amount or source.get("amount")
                if amount is not None and source.get("currency"):
                    cash_date = fact.effective_date or _message_date(parsed)
                    if cash_date is not None:
                        synthetic.append(
                            normalize_event({
                                **source,
                                "event_id": f"{source['event_id']}::refund::{cash_date}",
                                "direction": "credit",
                                "status": "settled",
                                "amount": amount,
                                "settlement_date": cash_date,
                                "event_date": cash_date,
                            })
                        )
                        applied.append(fact)
    return tuple(changed), tuple(applied), synthetic


def _synthetic_occurrences(
    templates: tuple[RecurringTemplate, ...],
    request_date: date,
    end_date: date,
    changes: tuple[SpendingChange, ...],
) -> list[NormalizedCashFlow]:
    change_by_id = {change.event_id: change for change in changes}
    occurrences: list[NormalizedCashFlow] = []
    for template in templates:
        current = template.last_date + timedelta(days=template.interval_days)
        while current <= end_date:
            if current >= request_date:
                change = change_by_id.get(template.template_id)
                if change is None:
                    amount = template.amount
                elif change.action == "stop":
                    amount = None
                else:
                    amount = change.amount
                if amount is not None:
                    occurrences.append(
                        NormalizedCashFlow(
                            event_id=f"{template.template_id}::recurrence::{current.isoformat()}",
                            user_id=template.user_id,
                            event_type=template.event_type,
                            category=template.category,
                            direction=template.direction,
                            amount=amount,
                            currency=template.currency,
                            event_date=current,
                            settlement_date=current,
                            # Projected recurring credits are modeled as confirmed
                            # forecast flows; raw scheduled credits remain excluded.
                            status="settled" if template.direction == "credit" else "scheduled",
                            linked_event_id=None,
                            flexibility=template.flexibility,
                            minimum_allowed_amount=template.minimum_allowed_amount,
                            cash_date=current,
                            signed_cash_impact=-amount if template.direction == "debit" else amount,
                        )
                    )
            current += timedelta(days=template.interval_days)
    return occurrences


def _profile_and_end(
    loaded_data: LoadedData, user_id: str, request_date: date, desired_completion_date: date | None
) -> tuple[dict, date]:
    return _profile_for_user(loaded_data, user_id), _forecast_end(request_date, desired_completion_date)


def _build(
    loaded_data: LoadedData,
    user_id: str,
    request_date: date,
    desired_completion_date: date | None,
    changes: tuple[SpendingChange, ...] = (),
) -> ForecastResult:
    profile, end_date = _profile_and_end(loaded_data, user_id, request_date, desired_completion_date)
    home_currency = profile["home_currency"]
    starting_balance = profile["current_available_balance"]
    minimum_balance = profile["minimum_balance_to_keep"]
    templates = infer_recurring_templates(loaded_data, user_id, request_date)
    parsed_messages = tuple(
        parse_messages(
            [row for row in loaded_data.tables.get("messages.csv", []) if row.get("user_id") == user_id],
            loaded_data,
        )
    )
    templates, applied_messages, message_flows = _apply_message_facts(
        loaded_data, user_id, templates, parsed_messages
    )
    salary_changes = [
        fact for fact in applied_messages
        if fact.kind == "salary_income" and fact.amount is not None and fact.currency
    ]
    employment_end = [
        fact.effective_date for fact in applied_messages
        if fact.kind == "employment_ended" and fact.effective_date is not None
    ]
    event_flows = []
    for event in loaded_data["financial_events.csv"]:
        if event.get("user_id") != user_id:
            continue
        settlement = event.get("settlement_date")
        if settlement is None or not (request_date <= settlement <= end_date):
            continue
        media_root = (
            loaded_data.dataset_dir / "media" / "images"
            if loaded_data.dataset_dir is not None
            else None
        )
        evidence = resolve_image_amount(
            event,
            loaded_data.tables.get("images.csv", ()),
            media_root,
        )
        event_for_normalization = event
        if evidence is not None:
            event_for_normalization = dict(event)
            event_for_normalization["amount"] = evidence.amount
        flow = normalize_event(event_for_normalization)
        if flow.cash_date is not None:
            if (
                flow.category == "salary"
                and flow.direction == "credit"
                and employment_end
                and flow.cash_date >= min(employment_end)
            ):
                continue
            applicable = [
                fact for fact in salary_changes
                if fact.effective_date is None or flow.cash_date >= fact.effective_date
            ]
            if applicable:
                amendment = applicable[-1]
                flow = replace(flow, amount=amendment.amount, currency=amendment.currency)
            event_flows.append(flow)
    all_flows = event_flows + message_flows + _synthetic_occurrences(templates, request_date, end_date, changes)
    converted_by_date: dict[date, list[ConvertedCashFlow]] = defaultdict(list)
    for flow in all_flows:
        converted = convert_cash_flow_to_home_currency(flow, home_currency, loaded_data)
        converted_by_date[flow.cash_date].append(converted)

    days: list[ForecastDay] = []
    balance = starting_balance
    current = request_date
    while current <= end_date:
        daily_flows = tuple(converted_by_date.get(current, ()))
        inflows = sum((item.converted_amount for item in daily_flows if item.converted_amount > 0), Decimal("0"))
        outflows = sum((-item.converted_amount for item in daily_flows if item.converted_amount < 0), Decimal("0"))
        opening = balance
        balance = opening + inflows - outflows
        days.append(ForecastDay(current, opening, inflows, outflows, balance, minimum_balance, daily_flows))
        current += timedelta(days=1)
    return ForecastResult(
        user_id, request_date, end_date, home_currency, starting_balance,
        minimum_balance, tuple(days), templates, applied_messages, changes,
    )


def build_baseline_forecast(
    loaded_data: LoadedData,
    user_id: str,
    request_date: date,
    desired_completion_date: date | None = None,
) -> ForecastResult:
    """Build a baseline forecast with conservative recurrence and amendments."""

    return _build(loaded_data, user_id, request_date, desired_completion_date)


def build_scenario_forecast(
    loaded_data: LoadedData,
    user_id: str,
    request_date: date,
    desired_completion_date: date | None = None,
    spending_changes: Iterable[str | SpendingChange] = (),
) -> ForecastResult:
    """Build a separate validated spending-change scenario over the baseline."""

    baseline = build_baseline_forecast(
        loaded_data, user_id, request_date, desired_completion_date
    )
    changes = _parse_spending_changes(
        spending_changes, baseline.recurring_templates, _profile_for_user(loaded_data, user_id)
    )
    scenario = _build(
        loaded_data, user_id, request_date, desired_completion_date, changes
    )
    return replace(scenario, baseline_forecast=baseline)


def balance_on(forecast: ForecastResult, target_date: date) -> Decimal:
    for day in forecast.days:
        if day.date == target_date:
            return day.closing_balance
    raise ValueError(f"Date is outside forecast window: {target_date}")


def minimum_projected_balance(
    forecast: ForecastResult, start_date: date | None = None, end_date: date | None = None
) -> Decimal:
    start = start_date or forecast.request_date
    end = end_date or forecast.end_date
    balances = [day.closing_balance for day in forecast.days if start <= day.date <= end]
    if not balances:
        raise ValueError("No forecast days in requested range")
    return min(balances)


def available_surplus(
    forecast: ForecastResult, start_date: date | None = None, end_date: date | None = None
) -> Decimal:
    return max(
        Decimal("0"),
        minimum_projected_balance(forecast, start_date, end_date)
        - forecast.minimum_required_balance,
    )
