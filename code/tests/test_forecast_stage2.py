"""Focused tests for recurrence, amendments, and spending scenarios."""

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from code.forecast import (
    balance_on,
    build_baseline_forecast,
    build_scenario_forecast,
)
from code.loaders import LoadedData


def profile(**overrides):
    row = {
        "user_id": "user_1",
        "home_currency": "USD",
        "current_available_balance": Decimal("1000"),
        "minimum_balance_to_keep": Decimal("100"),
        "expense_categories_user_is_willing_to_reduce": "dining",
        "expense_categories_user_is_willing_to_stop": "streaming",
    }
    row.update(overrides)
    return row


def event(event_id, settlement_date, **overrides):
    row = {
        "event_id": event_id,
        "user_id": "user_1",
        "event_type": "expense",
        "category": "dining",
        "description": "recurring",
        "direction": "debit",
        "amount": Decimal("10"),
        "currency": "USD",
        "event_date": settlement_date,
        "settlement_date": settlement_date,
        "status": "settled",
        "linked_event_id": None,
        "flexibility": "reducible",
        "minimum_allowed_amount": Decimal("3"),
    }
    row.update(overrides)
    return row


def data(events, messages=None, **profile_overrides):
    return LoadedData({
        "financial_profiles.csv": [profile(**profile_overrides)],
        "financial_events.csv": events,
        "exchange_rates.csv": [],
        "messages.csv": messages or [],
        "requests.csv": [],
        "sample_requests.csv": [],
    })


class ForecastStage2Tests(unittest.TestCase):
    def test_weekly_recurrence(self):
        events = [event(f"e{i}", date(2026, 1, 1) + __import__("datetime").timedelta(days=7 * i))
                  for i in range(3)]
        result = build_baseline_forecast(data(events), "user_1", date(2026, 1, 22))
        self.assertEqual(result.recurring_templates[0].interval_days, 7)
        self.assertEqual(balance_on(result, date(2026, 1, 29)), Decimal("980"))

    def test_biweekly_recurrence(self):
        events = [event(f"e{i}", date(2026, 1, 1) + __import__("datetime").timedelta(days=14 * i))
                  for i in range(3)]
        result = build_baseline_forecast(data(events), "user_1", date(2026, 2, 1))
        self.assertEqual(result.recurring_templates[0].interval_days, 14)

    def test_monthly_recurrence_and_irregular_records(self):
        monthly = [event(f"m{i}", date(2025, 9 + i, 1)) for i in range(3)]
        irregular = [event(f"i{i}", date(2025, 9, 1) + __import__("datetime").timedelta(days=d),
                           category="shopping") for i, d in enumerate((0, 10, 40))]
        result = build_baseline_forecast(data(monthly + irregular), "user_1", date(2025, 12, 1))
        self.assertEqual(len(result.recurring_templates), 1)
        self.assertEqual(result.recurring_templates[0].interval_days, 30)

    def test_cancelled_conflict_is_not_recurrence(self):
        events = [
            event("e1", date(2025, 10, 1)),
            event("e2", date(2025, 11, 1)),
            event("e3", date(2025, 12, 1), status="cancelled"),
        ]
        result = build_baseline_forecast(data(events), "user_1", date(2026, 1, 1))
        self.assertEqual(result.recurring_templates, ())

    def test_salary_amendment_effective_date(self):
        events = [
            event("s1", date(2025, 11, 15), event_type="income", category="salary",
                  direction="credit", amount=Decimal("100"), flexibility="fixed"),
            event("s2", date(2025, 12, 15), event_type="income", category="salary",
                  direction="credit", amount=Decimal("100"), flexibility="fixed"),
            event("s3", date(2026, 1, 15), event_type="income", category="salary",
                  direction="credit", amount=Decimal("100"), flexibility="fixed"),
        ]
        messages = [{
            "message_id": "msg",
            "user_id": "user_1",
            "request_id": None,
            "related_event_id": None,
            "sent_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "source_type": "employer",
            "message_text": "Your salary is now USD 150 effective 2026-02-15.",
        }]
        result = build_baseline_forecast(data(events, messages), "user_1", date(2026, 1, 20))
        self.assertEqual(balance_on(result, date(2026, 2, 15)), Decimal("1150"))
        self.assertEqual(len(result.applied_message_amendments), 1)

    def test_employment_ending_stops_future_salary(self):
        events = [
            event("s1", date(2025, 11, 15), event_type="income", category="salary",
                  direction="credit", amount=Decimal("100"), flexibility="fixed"),
            event("s2", date(2025, 12, 15), event_type="income", category="salary",
                  direction="credit", amount=Decimal("100"), flexibility="fixed"),
            event("s3", date(2026, 1, 15), event_type="income", category="salary",
                  direction="credit", amount=Decimal("100"), flexibility="fixed"),
        ]
        messages = [{
            "message_id": "msg",
            "user_id": "user_1",
            "request_id": None,
            "related_event_id": None,
            "sent_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "source_type": "employer",
            "message_text": "Employment has ended effective 2026-02-01.",
        }]
        result = build_baseline_forecast(data(events, messages), "user_1", date(2026, 1, 20))
        self.assertEqual(result.recurring_templates, ())

    def test_pending_refund_bonus_transfer_and_unrealized_are_excluded(self):
        events = [
            event("r", date(2026, 1, 25), event_type="refund", category="shopping",
                  direction="credit", status="pending", amount=Decimal("50")),
            event("i", date(2026, 1, 26), event_type="investment_valuation", category="investment",
                  direction="non_cash", status="unrealized", amount=Decimal("50")),
        ]
        messages = [{
            "message_id": "msg",
            "user_id": "user_1",
            "request_id": None,
            "related_event_id": None,
            "sent_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "source_type": "employer",
            "message_text": "Your bonus is pending and not approved.",
        }]
        result = build_baseline_forecast(data(events, messages), "user_1", date(2026, 1, 20))
        self.assertEqual(result.applied_message_amendments, ())
        self.assertEqual(balance_on(result, date(2026, 1, 26)), Decimal("1000"))

    def test_completed_refund_is_added(self):
        events = [event("r", date(2026, 1, 10), event_type="refund", category="shopping",
                        direction="credit", status="pending", amount=Decimal("50"))]
        messages = [{
            "message_id": "msg",
            "user_id": "user_1",
            "request_id": None,
            "related_event_id": "r",
            "sent_at": datetime(2026, 1, 20, tzinfo=timezone.utc),
            "source_type": "merchant",
            "message_text": "Your refund has been completed and credited to your account.",
        }]
        result = build_baseline_forecast(data(events, messages), "user_1", date(2026, 1, 20))
        self.assertEqual(balance_on(result, date(2026, 1, 20)), Decimal("1050"))

    def test_valid_stop_and_reduce_scenarios_preserve_baseline(self):
        stop_events = [
            event("x1", date(2025, 10, 1), category="streaming", flexibility="stoppable"),
            event("x2", date(2025, 11, 1), category="streaming", flexibility="stoppable"),
            event("x3", date(2025, 12, 1), category="streaming", flexibility="stoppable"),
        ]
        baseline = build_baseline_forecast(
            data(stop_events, expense_categories_user_is_willing_to_stop="streaming"),
            "user_1", date(2026, 1, 1),
        )
        scenario = build_scenario_forecast(
            data(stop_events, expense_categories_user_is_willing_to_stop="streaming"),
            "user_1", date(2026, 1, 1), spending_changes=["stop:x1"],
        )
        self.assertEqual(balance_on(baseline, date(2026, 2, 1)), Decimal("990"))
        self.assertEqual(balance_on(scenario, date(2026, 2, 1)), Decimal("1000"))
        reduced = build_scenario_forecast(
            data([event("x1", date(2025, 10, 1)), event("x2", date(2025, 11, 1)),
                  event("x3", date(2025, 12, 1))]),
            "user_1", date(2026, 1, 1), spending_changes=["reduce_to:x1:3"],
        )
        self.assertEqual(balance_on(reduced, date(2026, 2, 1)), Decimal("997"))

    def test_invalid_permission_and_stop_reduce_exclusivity(self):
        events = [event("x1", date(2025, 10, 1)), event("x2", date(2025, 11, 1)),
                  event("x3", date(2025, 12, 1))]
        with self.assertRaises(ValueError):
            build_scenario_forecast(data(events, expense_categories_user_is_willing_to_reduce=""),
                                    "user_1", date(2026, 1, 1), spending_changes=["reduce_to:x1:3"])
        with self.assertRaises(ValueError):
            build_scenario_forecast(data(events), "user_1", date(2026, 1, 1),
                                    spending_changes=["reduce_to:x1:3", "stop:x1"])

    def test_reduction_cannot_breach_minimum_allowed_amount(self):
        events = [event("x1", date(2025, 10, 1)), event("x2", date(2025, 11, 1)),
                  event("x3", date(2025, 12, 1))]
        with self.assertRaises(ValueError):
            build_scenario_forecast(data(events), "user_1", date(2026, 1, 1),
                                    spending_changes=["reduce_to:x1:2"])


if __name__ == "__main__":
    unittest.main()
