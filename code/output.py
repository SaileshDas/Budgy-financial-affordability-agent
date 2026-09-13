"""Deterministic serialization, explanations, and validation of output rows."""

from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Mapping

from code.decision import (
    ALLOWED_AFFORDABILITY_STATUSES,
    DecisionStage2Result,
)


OUTPUT_COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)
ALLOWED_PAYMENT_METHODS = frozenset(
    {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}
)
MONEY_QUANTUM = Decimal("0.01")


def format_money(value: Decimal) -> str:
    """Format monetary output without exposing Decimal's Python repr."""

    if not isinstance(value, Decimal):
        raise TypeError("money value must be a Decimal")
    return format(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP), "f")


def format_date(value: date | None) -> str:
    return "" if value is None else value.isoformat()


def format_plan_text(plan: str) -> str:
    if plan == "none":
        return plan
    formatted = []
    for payment in plan.split("|"):
        when, amount = payment.split(":", 1)
        formatted.append(f"{date.fromisoformat(when).isoformat()}:{format_money(Decimal(amount))}")
    return "|".join(formatted)


def _currency(request: Mapping[str, object], loaded_data) -> str:
    profiles = [
        row for row in loaded_data["financial_profiles.csv"]
        if row.get("user_id") == request.get("user_id")
    ]
    return str(profiles[0]["home_currency"]) if len(profiles) == 1 else ""


def generate_explanation(
    request: Mapping[str, object],
    result: DecisionStage2Result,
    loaded_data,
) -> str:
    """Generate a concise explanation from validated forecast facts."""

    currency = _currency(request, loaded_data)
    amount = format_money(request["requested_amount"])
    profiles = [
        row for row in loaded_data["financial_profiles.csv"]
        if row.get("user_id") == request.get("user_id")
    ]
    minimum = format_money(profiles[0]["minimum_balance_to_keep"]) if len(profiles) == 1 else "unavailable"
    if result.affordability_status == "affordable_now":
        if result.recommended_payment_method == "full_payment":
            detail = f"Pay {currency} {amount} now; the forecast preserves at least {currency} {minimum}."
        else:
            detail = (
                f"The full {currency} {amount} is safe today, and the selected "
                f"{result.recommended_payment_method} method preserves at least {currency} {minimum}."
            )
    elif result.affordability_status == "affordable_with_plan":
        detail = (
            f"Use {result.recommended_payment_method} to complete {currency} {amount} safely "
            f"by {request['desired_completion_date'].isoformat()} while preserving at least "
            f"{currency} {minimum}."
        )
    elif result.affordability_status == "affordable_later":
        detail = (
            f"Wait until {format_date(result.earliest_date_for_full_payment)} to pay "
            f"{currency} {amount}; paying earlier would risk the {currency} {minimum} minimum."
        )
    else:
        detail = (
            f"Do not complete the {currency} {amount} request by "
            f"{request['desired_completion_date'].isoformat()}; no safe option preserves "
            f"the {currency} {minimum} minimum."
        )
    if result.payment_plan != "none":
        detail += f" Payment plan: {result.payment_plan}."
    if result.spending_changes_needed != "none":
        detail += f" Required spending changes: {result.spending_changes_needed}."
    return detail


def blocked_row(request_id: str, error: Exception) -> dict[str, str]:
    """Represent unresolved requests without inventing financial values."""

    return {
        "request_id": request_id,
        "amount_safe_to_pay": "",
        "affordability_status": "",
        "recommended_payment_method": "",
        "payment_plan": "",
        "earliest_date_for_full_payment": "",
        "spending_changes_needed": "",
        "decision_explanation": (
            f"Decision could not be safely completed because required financial data "
            f"is unresolved or missing: {type(error).__name__}."
        ),
    }


def result_row(
    request: Mapping[str, object],
    result: DecisionStage2Result,
    loaded_data,
) -> dict[str, str]:
    return {
        "request_id": str(request["request_id"]),
        "amount_safe_to_pay": format_money(result.amount_safe_to_pay),
        "affordability_status": result.affordability_status,
        "recommended_payment_method": result.recommended_payment_method,
        "payment_plan": format_plan_text(result.payment_plan),
        "earliest_date_for_full_payment": format_date(
            result.earliest_date_for_full_payment
        ),
        "spending_changes_needed": result.spending_changes_needed,
        "decision_explanation": generate_explanation(request, result, loaded_data),
    }


def write_output(rows: list[Mapping[str, str]], path: str | Path) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def validate_output_rows(
    rows: list[Mapping[str, str]],
    requests: list[Mapping[str, object]],
    payment_options: list[Mapping[str, object]] | None = None,
) -> None:
    """Strictly validate safe rows and the explicit blocked-row convention."""

    expected = {str(row["request_id"]): row for row in requests}
    ids = [str(row.get("request_id", "")) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("output contains duplicate request_id values")
    if set(ids) != set(expected):
        raise ValueError("output does not represent exactly every request")
    for row in rows:
        if tuple(row.keys()) != OUTPUT_COLUMNS:
            raise ValueError("output columns or order are invalid")
        request = expected[row["request_id"]]
        fields = [row[name] for name in OUTPUT_COLUMNS[1:7]]
        if all(value == "" for value in fields):
            continue
        try:
            safe = Decimal(row["amount_safe_to_pay"])
        except Exception as error:
            raise ValueError("amount_safe_to_pay is not a valid Decimal") from error
        if safe < 0 or safe > request["requested_amount"]:
            raise ValueError("amount_safe_to_pay is outside the permitted range")
        if row["affordability_status"] not in ALLOWED_AFFORDABILITY_STATUSES:
            raise ValueError("invalid affordability_status")
        if row["recommended_payment_method"] not in ALLOWED_PAYMENT_METHODS:
            raise ValueError("invalid recommended_payment_method")
        if row["affordability_status"] == "affordable_now":
            if row["earliest_date_for_full_payment"] != request["request_date"].isoformat():
                raise ValueError("affordable_now must use request_date")
            if row["recommended_payment_method"] != "full_payment":
                raise ValueError("affordable_now must recommend full_payment")
        if row["earliest_date_for_full_payment"]:
            try:
                earliest = date.fromisoformat(row["earliest_date_for_full_payment"])
            except ValueError as error:
                raise ValueError("earliest date is not ISO formatted") from error
            if earliest > request["desired_completion_date"]:
                raise ValueError("earliest date is after the request deadline")
        if row["payment_plan"] == "":
            raise ValueError("payment_plan must be none when no plan is selected")
        if row["spending_changes_needed"] == "":
            raise ValueError("spending_changes_needed must be none when unchanged")
        if row["payment_plan"] != "none":
            payments = []
            for item in row["payment_plan"].split("|"):
                parts = item.split(":")
                if len(parts) != 2:
                    raise ValueError("payment_plan has invalid format")
                try:
                    payment_date = date.fromisoformat(parts[0])
                    payment_amount = Decimal(parts[1])
                except (ValueError, ArithmeticError) as error:
                    raise ValueError("payment_plan has invalid date or amount") from error
                if payment_amount <= 0:
                    raise ValueError("payment_plan contains a non-positive payment")
                payments.append((payment_date, payment_amount))
            if payments != sorted(payments):
                raise ValueError("payment_plan is not chronological")
            if row["recommended_payment_method"] == "partial_payment":
                if len(payments) != 2 or payments[0][0] != request["request_date"]:
                    raise ValueError("partial payment plan is invalid")
                if sum(amount for _, amount in payments) != request["requested_amount"]:
                    raise ValueError("partial payment plan does not complete the request")
            elif row["recommended_payment_method"] in {"full_payment", "installments"}:
                if payment_options is None:
                    raise ValueError("payment options are required to validate a selected plan")
                matching = [
                    option for option in payment_options
                    if option.get("request_id") == row["request_id"]
                    and option.get("payment_method") == row["recommended_payment_method"]
                ]
                if not any(_option_plan_text(option) == row["payment_plan"] for option in matching):
                    raise ValueError("selected payment plan is not a supplied option")
        if row["recommended_payment_method"] == "not_recommended" and row["payment_plan"] != "none":
            raise ValueError("not_recommended cannot contain a payment plan")


def _option_plan_text(option: Mapping[str, object]) -> str:
    frequency = option.get("payment_frequency_days") or 0
    count = option["number_of_payments"]
    first = option["first_payment_date"]
    amount = format_money(option["payment_amount"])
    return "|".join(
        f"{(first + timedelta(days=frequency * index)).isoformat()}:{amount}"
        for index in range(count)
    )


def validate_output_file(
    path: str | Path,
    requests: list[Mapping[str, object]],
    payment_options: list[Mapping[str, object]] | None = None,
) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != OUTPUT_COLUMNS:
            raise ValueError("output columns or order are invalid")
        rows = list(reader)
    validate_output_rows(rows, requests, payment_options)
    return rows
