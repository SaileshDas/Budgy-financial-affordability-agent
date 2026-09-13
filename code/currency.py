"""Exact dated currency conversion for normalized cash-flow records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from code.events import NormalizedCashFlow, UnresolvedAmountError, signed_cash_impact
from code.loaders import LoadedData


class CurrencyConversionError(ValueError):
    """Raised when a required currency conversion cannot be performed."""


@dataclass(frozen=True)
class ConvertedCashFlow:
    """A normalized cash flow with its signed amount in home currency."""

    cash_flow: NormalizedCashFlow
    home_currency: str
    converted_amount: Decimal


def lookup_exact_direct_rate(
    loaded_data: LoadedData,
    settlement_date: date | None,
    from_currency: str,
    to_currency: str,
) -> Decimal:
    """Return one exact direct rate for the settlement date and currency pair."""

    if settlement_date is None:
        raise CurrencyConversionError(
            "Cannot convert a foreign-currency amount without a settlement date"
        )
    if not from_currency or not to_currency:
        raise CurrencyConversionError("Currency codes are required for conversion")
    if from_currency == to_currency:
        raise CurrencyConversionError("A rate lookup is not needed for same currency")

    matches = [
        row
        for row in loaded_data["exchange_rates.csv"]
        if row.get("rate_date") == settlement_date
        and row.get("from_currency") == from_currency
        and row.get("to_currency") == to_currency
    ]
    if len(matches) != 1:
        raise CurrencyConversionError(
            f"No exact direct exchange rate for {settlement_date}: "
            f"{from_currency}->{to_currency}"
        )
    rate = matches[0].get("rate")
    if not isinstance(rate, Decimal) or rate <= 0:
        raise CurrencyConversionError(
            f"Invalid exchange rate for {settlement_date}: "
            f"{from_currency}->{to_currency}"
        )
    return rate


def convert_amount(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    settlement_date: date | None,
    loaded_data: LoadedData,
) -> Decimal:
    """Convert a Decimal amount using one exact direct dated rate."""

    if not isinstance(amount, Decimal):
        raise CurrencyConversionError("Conversion amounts must be Decimal values")
    if from_currency == to_currency:
        return amount
    return amount * lookup_exact_direct_rate(
        loaded_data, settlement_date, from_currency, to_currency
    )


def convert_cash_flow_to_home_currency(
    cash_flow: NormalizedCashFlow,
    home_currency: str,
    loaded_data: LoadedData,
) -> ConvertedCashFlow:
    """Convert a signed normalized cash flow while preserving its source record."""

    if cash_flow.amount is None:
        raise UnresolvedAmountError(
            f"Cash flow has unresolved amount: {cash_flow.event_id}"
        )
    signed_amount = signed_cash_impact(
        {
            "event_id": cash_flow.event_id,
            "amount": cash_flow.amount,
            "direction": cash_flow.direction,
            "status": cash_flow.status,
            "settlement_date": cash_flow.settlement_date,
        }
    )
    converted = convert_amount(
        signed_amount,
        cash_flow.currency,
        home_currency,
        cash_flow.settlement_date,
        loaded_data,
    )
    return ConvertedCashFlow(cash_flow, home_currency, converted)
