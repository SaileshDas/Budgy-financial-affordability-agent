"""Tests for exact dated currency conversion."""

from datetime import date
from decimal import Decimal
import unittest

from code.currency import (
    CurrencyConversionError,
    convert_amount,
    convert_cash_flow_to_home_currency,
    lookup_exact_direct_rate,
)
from code.events import normalize_event
from code.loaders import LoadedData


def loaded_with_rates(*rates):
    return LoadedData(
        {
            "exchange_rates.csv": [
                {
                    "rate_date": rate_date,
                    "from_currency": source,
                    "to_currency": target,
                    "rate": value,
                }
                for rate_date, source, target, value in rates
            ]
        }
    )


def cash_flow(**overrides):
    source = {
        "event_id": "event_1",
        "user_id": "user_1",
        "event_type": "expense",
        "category": "rent",
        "direction": "debit",
        "amount": Decimal("10.00"),
        "currency": "USD",
        "event_date": date(2026, 1, 1),
        "settlement_date": date(2026, 1, 15),
        "status": "settled",
        "linked_event_id": None,
        "flexibility": "fixed",
        "minimum_allowed_amount": None,
    }
    source.update(overrides)
    return normalize_event(source)


class CurrencyTests(unittest.TestCase):
    def test_same_currency_returns_original_decimal(self):
        amount = Decimal("10.123456")
        self.assertIs(
            convert_amount(amount, "USD", "USD", None, loaded_with_rates()),
            amount,
        )

    def test_exact_direct_conversion(self):
        loaded = loaded_with_rates(
            (date(2026, 1, 15), "USD", "EUR", Decimal("0.92"))
        )
        self.assertEqual(
            convert_amount(Decimal("10.00"), "USD", "EUR", date(2026, 1, 15), loaded),
            Decimal("9.2000"),
        )

    def test_missing_date_is_rejected(self):
        with self.assertRaises(CurrencyConversionError):
            convert_amount(Decimal("10"), "USD", "EUR", None, loaded_with_rates())

    def test_missing_direct_rate_is_rejected(self):
        with self.assertRaises(CurrencyConversionError):
            lookup_exact_direct_rate(
                loaded_with_rates(), date(2026, 1, 15), "USD", "EUR"
            )

    def test_inverse_only_rate_is_rejected(self):
        loaded = loaded_with_rates(
            (date(2026, 1, 15), "EUR", "USD", Decimal("1.09"))
        )
        with self.assertRaises(CurrencyConversionError):
            convert_amount(Decimal("10"), "USD", "EUR", date(2026, 1, 15), loaded)

    def test_cross_currency_rate_is_rejected(self):
        loaded = loaded_with_rates(
            (date(2026, 1, 15), "USD", "IDR", Decimal("15833.33")),
            (date(2026, 1, 15), "IDR", "EUR", Decimal("0.000058")),
        )
        with self.assertRaises(CurrencyConversionError):
            convert_amount(Decimal("10"), "USD", "EUR", date(2026, 1, 15), loaded)

    def test_decimal_precision_is_preserved(self):
        loaded = loaded_with_rates(
            (date(2026, 1, 15), "USD", "INR", Decimal("83.33"))
        )
        result = convert_amount(
            Decimal("1.2345"), "USD", "INR", date(2026, 1, 15), loaded
        )
        self.assertEqual(result, Decimal("102.870885"))
        self.assertIsInstance(result, Decimal)

    def test_settlement_date_is_used_for_lookup_and_source_is_preserved(self):
        loaded = loaded_with_rates(
            (date(2026, 1, 1), "USD", "EUR", Decimal("0.50")),
            (date(2026, 1, 15), "USD", "EUR", Decimal("0.92")),
        )
        source = cash_flow()
        result = convert_cash_flow_to_home_currency(source, "EUR", loaded)
        self.assertEqual(result.converted_amount, Decimal("-9.2000"))
        self.assertIs(result.cash_flow, source)
        self.assertEqual(result.home_currency, "EUR")


if __name__ == "__main__":
    unittest.main()
