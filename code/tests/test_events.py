"""Tests for financial-event normalization and cash-impact classification."""

from datetime import date
from decimal import Decimal
import unittest

from code.events import (
    UnresolvedAmountError,
    group_events_by_settlement_date,
    is_cash_impacting,
    normalize_event,
    signed_cash_impact,
    unresolved_events,
    get_events_for_user,
)


def event(**overrides):
    base = {
        "event_id": "event_1",
        "user_id": "user_1",
        "event_type": "expense",
        "category": "rent",
        "direction": "debit",
        "amount": Decimal("100.00"),
        "currency": "USD",
        "event_date": date(2026, 1, 1),
        "settlement_date": date(2026, 1, 3),
        "status": "settled",
        "linked_event_id": None,
        "flexibility": "fixed",
        "minimum_allowed_amount": None,
    }
    base.update(overrides)
    return base


class EventTests(unittest.TestCase):
    def test_settled_debit_has_negative_impact(self):
        item = event()
        self.assertTrue(is_cash_impacting(item))
        self.assertEqual(signed_cash_impact(item), Decimal("-100.00"))

    def test_settled_credit_has_positive_impact(self):
        item = event(direction="credit", event_type="income")
        self.assertEqual(signed_cash_impact(item), Decimal("100.00"))

    def test_pending_debit_impacts_cash(self):
        item = event(status="pending")
        self.assertTrue(is_cash_impacting(item))
        self.assertEqual(signed_cash_impact(item), Decimal("-100.00"))

    def test_scheduled_debit_impacts_cash(self):
        item = event(status="scheduled")
        self.assertTrue(is_cash_impacting(item))
        self.assertEqual(signed_cash_impact(item), Decimal("-100.00"))

    def test_failed_and_cancelled_events_do_not_impact_cash(self):
        for status in ("failed", "cancelled"):
            item = event(status=status)
            self.assertFalse(is_cash_impacting(item))
            self.assertEqual(signed_cash_impact(item), Decimal("0"))

    def test_unrealized_event_does_not_impact_cash(self):
        item = event(status="unrealized", direction="non_cash")
        self.assertFalse(is_cash_impacting(item))
        self.assertEqual(signed_cash_impact(item), Decimal("0"))

    def test_non_cash_event_does_not_impact_cash(self):
        item = event(direction="non_cash", event_type="investment_valuation")
        self.assertFalse(is_cash_impacting(item))
        self.assertEqual(signed_cash_impact(item), Decimal("0"))

    def test_missing_amount_is_unresolved_and_not_zero(self):
        item = event(amount=None)
        self.assertEqual(unresolved_events([item]), [item])
        with self.assertRaises(UnresolvedAmountError):
            signed_cash_impact(item)

    def test_settlement_date_is_used_in_normalized_flow(self):
        item = event(event_date=date(2026, 1, 1), settlement_date=date(2026, 1, 10))
        flow = normalize_event(item)
        self.assertEqual(flow.cash_date, date(2026, 1, 10))
        grouped = group_events_by_settlement_date([item])
        self.assertIn(date(2026, 1, 10), grouped)
        self.assertNotIn(date(2026, 1, 1), grouped)

    def test_helpers_filter_users(self):
        first = event()
        second = event(event_id="event_2", user_id="user_2")
        self.assertEqual(get_events_for_user([first, second], "user_1"), [first])


if __name__ == "__main__":
    unittest.main()
