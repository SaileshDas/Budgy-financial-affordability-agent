"""Stage 1 deterministic affordability calculations over a baseline forecast."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Mapping

from code.forecast import ForecastResult, build_baseline_forecast, build_scenario_forecast
from code.loaders import LoadedData


ALLOWED_AFFORDABILITY_STATUSES = frozenset(
    {
        "affordable_now",
        "affordable_with_plan",
        "affordable_later",
        "not_affordable",
    }
)


@dataclass(frozen=True)
class DecisionStage1Result:
    """Safe-payment capacity and timing derived from a baseline forecast."""

    amount_safe_to_pay: Decimal
    affordability_status: str
    earliest_date_for_full_payment: date | None


@dataclass(frozen=True)
class ValidatedPaymentPlan:
    payment_option_id: str
    payment_method: str
    payments: tuple[tuple[date, Decimal], ...]
    payment_amount: Decimal
    number_of_payments: int
    financing_fee: Decimal
    total_payable_amount: Decimal
    spending_changes: tuple[str, ...] = ()

    @property
    def completion_date(self) -> date:
        return self.payments[-1][0]

    @property
    def start_date(self) -> date:
        return self.payments[0][0]


@dataclass(frozen=True)
class DecisionStage2Result(DecisionStage1Result):
    recommended_payment_method: str = "not_recommended"
    selected_payment_option_id: str | None = None
    payment_plan: str = "none"
    spending_changes_needed: str = "none"
    baseline_result: DecisionStage1Result | None = None
    selected_payment_option: ValidatedPaymentPlan | None = None
    selected_forecast: ForecastResult | None = None


def _require_decimal(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    return value


def _validate_request_amount(requested_amount: Decimal) -> Decimal:
    amount = _require_decimal(requested_amount, "requested_amount")
    if amount < Decimal("0"):
        raise ValueError("requested_amount cannot be negative")
    return amount


def _deadline(forecast: ForecastResult, desired_completion_date: date | None) -> date:
    deadline = desired_completion_date or forecast.end_date
    if deadline < forecast.request_date:
        raise ValueError("desired_completion_date cannot precede request_date")
    return min(deadline, forecast.end_date)


def _minimum_surplus_from(
    forecast: ForecastResult,
    start_date: date,
    end_date: date,
) -> Decimal:
    balances = [
        day.closing_balance - forecast.minimum_required_balance
        for day in forecast.days
        if start_date <= day.date <= end_date
    ]
    if not balances:
        raise ValueError("Requested date range is outside the forecast")
    return min(balances)


def amount_safe_to_pay(
    forecast: ForecastResult,
    requested_amount: Decimal,
) -> Decimal:
    """Return the maximum requested amount payable on the request date.

    The amount is constrained by the lowest baseline balance over the complete
    forecast horizon, so future confirmed obligations remain protected.
    """

    requested = _validate_request_amount(requested_amount)
    surplus = _minimum_surplus_from(
        forecast, forecast.request_date, forecast.end_date
    )
    return min(requested, max(Decimal("0"), surplus))


def earliest_date_for_full_payment(
    forecast: ForecastResult,
    requested_amount: Decimal,
    desired_completion_date: date | None = None,
) -> date | None:
    """Find the first date a single full payment preserves the minimum balance."""

    requested = _validate_request_amount(requested_amount)
    deadline = _deadline(forecast, desired_completion_date)
    if requested == Decimal("0"):
        return forecast.request_date

    # A payment cannot repair a baseline breach that occurs before it.
    if _minimum_surplus_from(forecast, forecast.request_date, forecast.request_date) < 0:
        return None

    for day in forecast.days:
        if day.date < forecast.request_date or day.date > deadline:
            continue
        prior_days = [
            item.closing_balance - forecast.minimum_required_balance
            for item in forecast.days
            if forecast.request_date <= item.date < day.date
        ]
        if prior_days and min(prior_days) < 0:
            return None
        if _minimum_surplus_from(forecast, day.date, forecast.end_date) >= requested:
            return day.date
    return None


def affordability_status(
    forecast: ForecastResult,
    requested_amount: Decimal,
    desired_completion_date: date | None = None,
) -> str:
    """Classify baseline affordability without selecting a payment plan."""

    requested = _validate_request_amount(requested_amount)
    if amount_safe_to_pay(forecast, requested) >= requested:
        return "affordable_now"
    if earliest_date_for_full_payment(
        forecast, requested, desired_completion_date
    ) is not None:
        return "affordable_later"
    return "not_affordable"


def evaluate_stage1_decision(
    forecast: ForecastResult,
    requested_amount: Decimal,
    desired_completion_date: date | None = None,
) -> DecisionStage1Result:
    """Return all Stage 1 decision outputs from one baseline forecast."""

    return DecisionStage1Result(
        amount_safe_to_pay=amount_safe_to_pay(forecast, requested_amount),
        affordability_status=affordability_status(
            forecast, requested_amount, desired_completion_date
        ),
        earliest_date_for_full_payment=earliest_date_for_full_payment(
            forecast, requested_amount, desired_completion_date
        ),
    )


def _profile_for_user(loaded_data: LoadedData, user_id: str) -> Mapping[str, object]:
    matches = [row for row in loaded_data["financial_profiles.csv"] if row.get("user_id") == user_id]
    if len(matches) != 1:
        raise ValueError(f"Expected one financial profile for user: {user_id}")
    return matches[0]


def _allowed_methods(profile: Mapping[str, object]) -> set[str]:
    return {part.strip() for part in str(profile.get("payment_methods_user_will_consider") or "").split("|") if part.strip()}


def _option_decimal(option: Mapping[str, object], field: str) -> Decimal:
    value = option.get(field)
    if not isinstance(value, Decimal):
        raise ValueError(f"{field} must be a Decimal")
    return value


def _validate_option(
    option: Mapping[str, object],
    request_id: str,
    requested_amount: Decimal,
    deadline: date,
    profile: Mapping[str, object],
) -> ValidatedPaymentPlan:
    required = (
        "payment_option_id", "request_id", "payment_method", "payment_amount",
        "number_of_payments", "first_payment_date", "financing_fee",
        "total_payable_amount",
    )
    if any(option.get(field) is None for field in required):
        raise ValueError("Payment option is missing required data")
    if option.get("request_id") != request_id:
        raise ValueError("Payment option belongs to another request")
    method = str(option["payment_method"])
    payment_amount = _option_decimal(option, "payment_amount")
    fee = _option_decimal(option, "financing_fee")
    total = _option_decimal(option, "total_payable_amount")
    count = option["number_of_payments"]
    first = option["first_payment_date"]
    frequency = option.get("payment_frequency_days")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0 or not isinstance(first, date):
        raise ValueError("Invalid payment count or first date")
    if method == "full_payment":
        if count != 1 or frequency is not None or payment_amount != requested_amount or total != payment_amount:
            raise ValueError("Invalid full-payment option")
    elif method == "installments":
        if count < 2 or not isinstance(frequency, int) or frequency not in {28, 30, 31}:
            raise ValueError("Invalid installment option")
        max_months = profile.get("max_installment_months")
        if max_months is not None and (not isinstance(max_months, int) or count > max_months):
            raise ValueError("Installment count exceeds user limit")
    else:
        raise ValueError(f"Unsupported payment method: {method}")
    if payment_amount <= 0 or fee < 0 or total <= 0:
        raise ValueError("Invalid payment values")
    if method == "installments" and payment_amount * count < requested_amount:
        raise ValueError("Installment schedule does not cover requested amount")
    payments = tuple((first + timedelta(days=(frequency or 0) * index), payment_amount) for index in range(count))
    if payments[-1][0] > deadline:
        raise ValueError("Payment plan completes after deadline")
    return ValidatedPaymentPlan(
        str(option["payment_option_id"]), method, payments, payment_amount,
        count, fee, total,
    )


def validate_payment_options(
    loaded_data: LoadedData, request: Mapping[str, object]
) -> tuple[ValidatedPaymentPlan, ...]:
    """Validate only the supplied options for the request."""
    request_id = str(request["request_id"])
    requested = _validate_request_amount(request["requested_amount"])
    deadline = request["desired_completion_date"]
    request_date = request.get("request_date")
    if not isinstance(deadline, date) or not isinstance(request_date, date):
        raise ValueError("request_date and desired_completion_date must be dates")
    profile = _profile_for_user(loaded_data, str(request["user_id"]))
    valid = []
    for option in loaded_data["request_payment_options.csv"]:
        if option.get("request_id") != request_id:
            continue
        try:
            plan = _validate_option(option, request_id, requested, deadline, profile)
            if plan.start_date < request_date:
                raise ValueError("Payment plan starts before request date")
            valid.append(plan)
        except ValueError:
            continue
    return tuple(valid)


def _simulate_plan(forecast: ForecastResult, plan: ValidatedPaymentPlan) -> bool:
    payments: dict[date, Decimal] = {}
    for payment_date, amount in plan.payments:
        payments[payment_date] = payments.get(payment_date, Decimal("0")) + amount
    adjustment = Decimal("0")
    for day in forecast.days:
        adjustment += payments.get(day.date, Decimal("0"))
        if day.closing_balance - adjustment < forecast.minimum_required_balance:
            return False
    return True


def _plan_text(plan: ValidatedPaymentPlan) -> str:
    return "|".join(f"{when.isoformat()}:{amount}" for when, amount in plan.payments)


def _candidate_rank(plan: ValidatedPaymentPlan) -> tuple[date, int, Decimal, date, int, str]:
    return (
        plan.completion_date,
        len(plan.spending_changes),
        plan.total_payable_amount,
        plan.start_date,
        plan.number_of_payments,
        plan.payment_option_id,
    )


def _scenario_changes(forecast: ForecastResult) -> tuple[tuple[str, ...], ...]:
    changes = []
    for template in forecast.recurring_templates:
        if template.direction != "debit":
            continue
        if template.flexibility in {"stoppable", "reducible_or_stoppable"}:
            changes.append(f"stop:{template.template_id}")
        if template.flexibility in {"reducible", "reducible_or_stoppable"} and template.minimum_allowed_amount is not None:
            if template.minimum_allowed_amount < template.amount:
                changes.append(f"reduce_to:{template.template_id}:{template.minimum_allowed_amount}")
    scenarios = [()]
    for size in range(1, min(3, len(changes)) + 1):
        scenarios.extend(combinations(changes, size))
    return tuple(scenarios)


def _select_plan(
    loaded_data: LoadedData,
    request: Mapping[str, object],
    baseline: ForecastResult,
    options: tuple[ValidatedPaymentPlan, ...],
) -> tuple[ValidatedPaymentPlan, ForecastResult] | None:
    profile = _profile_for_user(loaded_data, str(request["user_id"]))
    allowed = _allowed_methods(profile)
    candidates = []
    requested = _validate_request_amount(request["requested_amount"])
    baseline_result = evaluate_stage1_decision(
        baseline, requested, request["desired_completion_date"]
    )
    if (
        "partial_payment" in allowed
        and request.get("allows_partial_payment") is True
        and Decimal("0") < baseline_result.amount_safe_to_pay < requested
        and baseline_result.earliest_date_for_full_payment is not None
        and baseline_result.earliest_date_for_full_payment <= request["desired_completion_date"]
    ):
        partial = ValidatedPaymentPlan(
            "partial_payment",
            "partial_payment",
            (
                (baseline.request_date, baseline_result.amount_safe_to_pay),
                (
                    baseline_result.earliest_date_for_full_payment,
                    requested - baseline_result.amount_safe_to_pay,
                ),
            ),
            baseline_result.amount_safe_to_pay,
            2,
            Decimal("0"),
            requested,
        )
        if _simulate_plan(baseline, partial):
            candidates.append((partial, baseline))
    for option in options:
        if option.payment_method not in allowed:
            continue
        for changes in _scenario_changes(baseline):
            try:
                scenario = baseline if not changes else build_scenario_forecast(
                    loaded_data, str(request["user_id"]), request["request_date"],
                    request["desired_completion_date"], changes,
                )
            except ValueError:
                continue
            candidate = replace(option, spending_changes=changes)
            if _simulate_plan(scenario, candidate):
                candidates.append((candidate, scenario))
    return min(candidates, key=lambda item: _candidate_rank(item[0])) if candidates else None


def evaluate_stage2_decision(
    loaded_data: LoadedData, request: Mapping[str, object]
) -> DecisionStage2Result:
    """Evaluate supplied plans and permitted spending-change scenarios."""
    requested = _validate_request_amount(request["requested_amount"])
    request_date = request["request_date"]
    deadline = request["desired_completion_date"]
    if not isinstance(request_date, date) or not isinstance(deadline, date):
        raise ValueError("request_date and desired_completion_date must be dates")
    user_id = str(request["user_id"])
    baseline = build_baseline_forecast(loaded_data, user_id, request_date, deadline)
    baseline_result = evaluate_stage1_decision(baseline, requested, deadline)
    selected = _select_plan(loaded_data, request, baseline, validate_payment_options(loaded_data, request))
    allowed_methods = _allowed_methods(_profile_for_user(loaded_data, user_id))
    if baseline_result.affordability_status == "affordable_now" and "full_payment" in allowed_methods:
        status = "affordable_now"
    elif selected is not None:
        status = "affordable_with_plan"
    elif baseline_result.earliest_date_for_full_payment is not None and "full_payment" in allowed_methods:
        status = "affordable_later"
    else:
        status = "not_affordable"
    method = "not_recommended"
    option_id = None
    payment_plan = "none"
    changes = "none"
    selected_option = None
    selected_forecast = None
    if selected is not None:
        selected_option, selected_forecast = selected
        method = selected_option.payment_method
        option_id = selected_option.payment_option_id
        payment_plan = _plan_text(selected_option)
        changes = "|".join(selected_option.spending_changes) or "none"
    elif status == "affordable_later":
        method = "wait"
    return DecisionStage2Result(
        amount_safe_to_pay=baseline_result.amount_safe_to_pay,
        affordability_status=status,
        earliest_date_for_full_payment=baseline_result.earliest_date_for_full_payment,
        recommended_payment_method=method,
        selected_payment_option_id=option_id,
        payment_plan=payment_plan,
        spending_changes_needed=changes,
        baseline_result=baseline_result,
        selected_payment_option=selected_option,
        selected_forecast=selected_forecast,
    )


# Explicit aliases make the public responsibilities discoverable without
# coupling callers to the internal result object.
calculate_amount_safe_to_pay = amount_safe_to_pay
determine_affordability_status = affordability_status
determine_earliest_date_for_full_payment = earliest_date_for_full_payment
evaluate_payment_options = validate_payment_options
decide_request = evaluate_stage2_decision
