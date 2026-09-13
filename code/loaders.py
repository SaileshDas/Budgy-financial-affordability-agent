"""CSV loading, parsing, and input-schema validation helpers."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd


class LoaderError(ValueError):
    """Base error for invalid loader input."""


class MissingInputFileError(FileNotFoundError, LoaderError):
    """Raised when a required dataset file is missing."""


class MissingColumnError(LoaderError):
    """Raised when a required CSV column is missing."""


REQUIRED_FILES = (
    "exchange_rates.csv",
    "financial_events.csv",
    "financial_profiles.csv",
    "images.csv",
    "messages.csv",
    "request_payment_options.csv",
    "requests.csv",
    "sample_requests.csv",
)

REQUIRED_COLUMNS = {
    "exchange_rates.csv": ("rate_date", "from_currency", "to_currency", "rate"),
    "financial_events.csv": (
        "event_id",
        "user_id",
        "event_type",
        "description",
        "category",
        "direction",
        "amount",
        "currency",
        "event_date",
        "settlement_date",
        "status",
        "linked_event_id",
        "flexibility",
        "minimum_allowed_amount",
    ),
    "financial_profiles.csv": (
        "user_id",
        "home_currency",
        "current_available_balance",
        "minimum_balance_to_keep",
        "financial_priorities",
        "expense_categories_to_protect",
        "expense_categories_user_is_willing_to_reduce",
        "expense_categories_user_is_willing_to_stop",
        "payment_methods_user_will_consider",
        "max_installment_months",
    ),
    "images.csv": ("image_id", "user_id", "request_id", "related_event_id"),
    "messages.csv": (
        "message_id",
        "user_id",
        "request_id",
        "related_event_id",
        "sent_at",
        "source_type",
        "message_text",
    ),
    "output.csv": (
        "request_id",
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
        "decision_explanation",
    ),
    "request_payment_options.csv": (
        "payment_option_id",
        "request_id",
        "payment_method",
        "payment_amount",
        "number_of_payments",
        "first_payment_date",
        "payment_frequency_days",
        "financing_fee",
        "total_payable_amount",
    ),
    "requests.csv": (
        "request_id",
        "user_id",
        "request_date",
        "request_type",
        "requested_amount",
        "desired_completion_date",
        "allows_partial_payment",
        "request_text",
    ),
    "sample_requests.csv": (
        "request_id",
        "user_id",
        "request_date",
        "request_type",
        "requested_amount",
        "desired_completion_date",
        "allows_partial_payment",
        "request_text",
        "amount_safe_to_pay",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
        "decision_explanation",
    ),
}

_DATE_COLUMNS = {
    "rate_date",
    "event_date",
    "settlement_date",
    "request_date",
    "desired_completion_date",
    "first_payment_date",
    "earliest_date_for_full_payment",
}
_TIMESTAMP_COLUMNS = {"sent_at"}
_DECIMAL_COLUMNS = {
    "rate",
    "amount",
    "current_available_balance",
    "minimum_balance_to_keep",
    "minimum_allowed_amount",
    "requested_amount",
    "payment_amount",
    "financing_fee",
    "total_payable_amount",
    "amount_safe_to_pay",
}
_INTEGER_COLUMNS = {"max_installment_months", "number_of_payments", "payment_frequency_days"}
_BOOLEAN_COLUMNS = {"allows_partial_payment"}


@dataclass(frozen=True)
class LoadedData:
    """All required CSV tables keyed by their file name."""

    tables: dict[str, list[dict[str, Any]]]
    dataset_dir: Path | None = None

    def __getitem__(self, filename: str) -> list[dict[str, Any]]:
        return self.tables[filename]


def default_dataset_dir() -> Path:
    """Return the repository dataset directory relative to this module."""

    return Path(__file__).resolve().parent.parent / "dataset"


def load_all(dataset_dir: str | Path | None = None) -> LoadedData:
    """Load, validate, and type-normalize every required dataset CSV."""

    root = Path(dataset_dir) if dataset_dir is not None else default_dataset_dir()
    tables: dict[str, list[dict[str, Any]]] = {}
    for filename in REQUIRED_FILES:
        path = root / filename
        if not path.is_file():
            raise MissingInputFileError(f"Required dataset file is missing: {path}")
        tables[filename] = _load_csv(path, REQUIRED_COLUMNS[filename])
    return LoadedData(tables, root)


def _load_csv(path: Path, required_columns: tuple[str, ...]) -> list[dict[str, Any]]:
    table = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        encoding="utf-8-sig",
    )
    missing = [column for column in required_columns if column not in table.columns]
    if missing:
        raise MissingColumnError(f"{path.name} is missing required columns: {', '.join(missing)}")
    rows = table.to_dict(orient="records")
    return [{key: _normalize_value(key, value) for key, value in row.items()} for row in rows]


def _normalize_value(column: str, value: str | None) -> Any:
    if value is None or value.strip() == "":
        return None
    value = value.strip()
    if column in _DATE_COLUMNS:
        return _parse_date(value)
    if column in _TIMESTAMP_COLUMNS:
        return _parse_timestamp(value)
    if column in _DECIMAL_COLUMNS:
        return Decimal(value)
    if column in _INTEGER_COLUMNS:
        return int(value)
    if column in _BOOLEAN_COLUMNS:
        if value.lower() in {"true", "1"}:
            return True
        if value.lower() in {"false", "0"}:
            return False
        raise LoaderError(f"Invalid boolean value for {column}: {value!r}")
    return value


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        match = re.fullmatch(r"(\d{4})\D*(\d{1,2})\D*(\d{1,2})", value)
        if not match:
            raise LoaderError(f"Invalid date value: {value!r}") from None
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            raise LoaderError(f"Invalid date value: {value!r}") from None


def _parse_timestamp(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise LoaderError(f"Invalid timestamp value: {value!r}") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
