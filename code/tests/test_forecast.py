"""Tests for baseline cash-flow forecasting."""

from datetime import date
from decimal import Decimal
import unittest

from code.events import UnresolvedAmountError
from code.forecast import (
    available_surplus,
    balance_on,
    build_baseline_forecast,
    minimum_projected_balance,
)
from code.loaders import LoadedData


REQUEST_DATE = date(2026, 1, 1)


def profile(**overrides):
    row = {
        "user_id": "user_1",
        "home_currency": "USD",
        "current_available_balance": Decimal("1000.00"),
        "minimum_balance_to_keep": Decimal("200.00"),
    }
    row.update(overrides)
    return row


def event(**overrides):
    row = {
        "event_id": "event_1",
        "user_id": "user_1",
        "event_type": "expense",
        "category": "rent",
        "description": "Test event",
        "direction": "debit",
        "amount": Decimal("100.00"),
        "currency": "USD",
        "event_date": REQUEST_DATE,
        "settlement_date": REQUEST_DATE,
        "status": "settled",
        "linked_event_id": None,
        "flexibility": "fixed",
        "minimum_allowed_amount": None,
    }
    row.update(overrides)
    return row


def loaded(events=None, rates=None, **profile_overrides):
    return LoadedData(
        {
            "financial_profiles.csv": [profile(**profile_overrides)],
            "financial_events.csv": list(events or []),
            "exchange_rates.csv": list(rates or []),
        }
    )


class ForecastTests(unittest.TestCase):
    def test_starting_balance(self):
        result = build_baseline_forecast(loaded(), "user_1", REQUEST_DATE)
        self.assertEqual(result.days[0].opening_balance, Decimal("1000.00"))
        self.assertEqual(result.starting_balance, Decimal("1000.00"))

    def test_settled_debit_and_credit(self):
        result = build_baseline_forecast(
            loaded([
                event(event_id="debit", amount=Decimal("100.00")),
                event(
                    event_id="credit",
                    event_type="income",
                    category="salary",
                    direction="credit",
                    amount=Decimal("250.00"),
                ),
            ]),
            "user_1",
            REQUEST_DATE,
        )
        self.assertEqual(result.days[0].cash_inflows, Decimal("250.00"))
        self.assertEqual(result.days[0].cash_outflows, Decimal("100.00"))
        self.assertEqual(result.days[0].closing_balance, Decimal("1150.00"))

    def test_pending_and_scheduled_debits(self):
        result = build_baseline_forecast(
            loaded([
                event(event_id="pending", status="pending", amount=Decimal("25")),
                event(event_id="scheduled", status="scheduled", amount=Decimal("35")),
            ]),
            "user_1",
            REQUEST_DATE,
        )
        self.assertEqual(result.days[0].cash_outflows, Decimal("60"))

    def test_pending_credit_is_excluded(self):
        result = build_baseline_forecast(
            loaded([event(direction="credit", status="pending", amount=Decimal("500"))]),
            "user_1",
            REQUEST_DATE,
        )
        self.assertEqual(result.days[0].closing_balance, Decimal("1000.00"))

    def test_failed_cancelled_unrealized_and_non_cash_are_excluded(self):
        events = [
            event(event_id="failed", status="failed", amount=Decimal("10")),
            event(event_id="cancelled", status="cancelled", amount=Decimal("20")),
            event(
                event_id="unrealized",
                status="unrealized",
                direction="non_cash",
                event_type="investment_valuation",
                amount=Decimal("30"),
            ),
            event(
                event_id="non_cash",
                direction="non_cash",
                event_type="investment_valuation",
                amount=Decimal("40"),
            ),
        ]
        result = build_baseline_forecast(loaded(events), "user_1", REQUEST_DATE)
        self.assertEqual(result.days[0].closing_balance, Decimal("1000.00"))

    def test_settlement_date_controls_cash_date(self):
        result = build_baseline_forecast(
            loaded([event(
                event_date=REQUEST_DATE,
                settlement_date=date(2026, 1, 3),
                amount=Decimal("75"),
            )]),
            "user_1",
            REQUEST_DATE,
        )
        self.assertEqual(balance_on(result, date(2026, 1, 1)), Decimal("1000.00"))
        self.assertEqual(balance_on(result, date(2026, 1, 3)), Decimal("925.00"))

    def test_foreign_currency_conversion(self):
        result = build_baseline_forecast(
            loaded(
                [event(
                    currency="EUR",
                    settlement_date=date(2026, 1, 2),
                    amount=Decimal("10.00"),
                )],
                [{"rate_date": date(2026, 1, 2), "from_currency": "EUR",
                  "to_currency": "USD", "rate": Decimal("1.10")}],
            ),
            "user_1",
            REQUEST_DATE,
        )
        self.assertEqual(balance_on(result, date(2026, 1, 2)), Decimal("989.000"))

    def test_missing_amount_raises(self):
        with self.assertRaises(UnresolvedAmountError):
            build_baseline_forecast(
                loaded([event(amount=None)]), "user_1", REQUEST_DATE
            )

    def test_minimum_balance_and_surplus(self):
        result = build_baseline_forecast(
            loaded([event(amount=Decimal("850"))]),
            "user_1",
            REQUEST_DATE,
        )
        self.assertEqual(minimum_projected_balance(result), Decimal("150.00"))
        self.assertEqual(available_surplus(result), Decimal("0"))

    def test_ninety_day_horizon(self):
        result = build_baseline_forecast(loaded(), "user_1", REQUEST_DATE)
        self.assertEqual(result.end_date, date(2026, 4, 1))
        self.assertEqual(len(result.days), 91)

    def test_deadline_extends_horizon(self):
        deadline = date(2026, 6, 15)
        result = build_baseline_forecast(loaded(), "user_1", REQUEST_DATE, deadline)
        self.assertEqual(result.end_date, deadline)
        self.assertEqual(len(result.days), (deadline - REQUEST_DATE).days + 1)

    def test_same_day_cash_flows(self):
        result = build_baseline_forecast(
            loaded([
                event(event_id="debit", amount=Decimal("10.10")),
                event(
                    event_id="credit",
                    direction="credit",
                    event_type="income",
                    category="salary",
                    amount=Decimal("20.20"),
                ),
            ]),
            "user_1",
            REQUEST_DATE,
        )
        day = result.days[0]
        self.assertEqual(day.opening_balance, Decimal("1000.00"))
        self.assertEqual(day.closing_balance, Decimal("1010.10"))

    def test_decimal_precision(self):
        result = build_baseline_forecast(
            loaded([event(amount=Decimal("0.01"))]), "user_1", REQUEST_DATE
        )
        self.assertEqual(result.days[0].closing_balance, Decimal("999.99"))
        self.assertIsInstance(result.days[0].closing_balance, Decimal)


if __name__ == "__main__":
    unittest.main()
