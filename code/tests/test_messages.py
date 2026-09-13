"""Tests for deterministic message parsing."""

from datetime import date
from decimal import Decimal
import unittest

from code.loaders import LoadedData
from code.messages import parse_message


def loaded(event_ids=("event_1",)):
    return LoadedData({
        "financial_profiles.csv": [{"user_id": "user_1"}],
        "requests.csv": [{"request_id": "request_1"}],
        "sample_requests.csv": [],
        "financial_events.csv": [{"event_id": event_id} for event_id in event_ids],
    })


def message(text, **overrides):
    row = {
        "message_id": "message_1",
        "user_id": "user_1",
        "request_id": None,
        "related_event_id": None,
        "sent_at": None,
        "source_type": "employer",
        "message_text": text,
    }
    row.update(overrides)
    return row


class MessageParserTests(unittest.TestCase):
    def fact(self, text, **kwargs):
        result = parse_message(message(text, **kwargs), loaded())
        self.assertEqual(result.disposition, "parsed")
        self.assertTrue(result.facts)
        return result.facts

    def test_english_salary_amendment(self):
        facts = self.fact("Your salary is now USD 1250.50 effective 2026-09-15.")
        self.assertEqual(facts[0].kind, "salary_income")
        self.assertEqual(facts[0].amount, Decimal("1250.50"))
        self.assertEqual(facts[0].currency, "USD")

    def test_indonesian_salary_amendment(self):
        facts = self.fact("Gaji bulanan Anda naik menjadi IDR 42750000. Berlaku mulai 2025-08-15.")
        self.assertEqual(facts[0].amount, Decimal("42750000"))
        self.assertEqual(facts[0].effective_date, date(2025, 8, 15))

    def test_salary_date_amendment(self):
        facts = self.fact("Your confirmed salary is now expected on 2026-09-23. This replaces the earlier date.")
        self.assertEqual(facts[0].kind, "salary_payment_date")
        self.assertEqual(facts[0].action, "override")

    def test_employment_ending(self):
        facts = self.fact("Your employment has ended. No regular salary payments are scheduled after the final settlement.")
        self.assertEqual(facts[0].kind, "employment_ended")
        self.assertFalse(facts[0].spendable)

    def test_pending_and_completed_refunds(self):
        pending = self.fact("Your refund has been initiated but has not reached your account yet.", related_event_id="event_1")
        self.assertEqual(pending[0].status, "pending")
        self.assertFalse(pending[0].spendable)
        completed = self.fact("Your refund has been completed and credited to your account.", related_event_id="event_1")
        self.assertTrue(completed[0].spendable)

    def test_failed_debit_and_dispute(self):
        failed = self.fact("The previous debit attempt failed. Another debit will be attempted.", related_event_id="event_1")
        self.assertEqual(failed[0].kind, "failed_debit")
        dispute = self.fact("The dispute is open and no reversal has been posted yet.", related_event_id="event_1")
        self.assertEqual(dispute[0].status, "open")

    def test_internal_transfer(self):
        facts = self.fact("The matching debit and credit came from a transfer between your two accounts.")
        self.assertFalse(facts[0].spendable)

    def test_unrealized_and_realized_investments(self):
        unrealized = self.fact("No units have been sold and no cash proceeds have been generated.")
        self.assertEqual(unrealized[0].status, "unrealized")
        realized = self.fact("The proceeds from your investment sale have settled in the cash account. Amount USD 100.25.")
        self.assertEqual(realized[0].kind, "investment_sale")
        self.assertEqual(realized[0].amount, Decimal("100.25"))

    def test_pending_income_is_not_spendable(self):
        facts = self.fact("Your quarterly bonus is pending and not approved.")
        self.assertFalse(facts[0].spendable)

    def test_related_event_validation(self):
        result = parse_message(message("Refund completed.", related_event_id="missing"), loaded())
        self.assertEqual(result.disposition, "ambiguous")
        self.assertEqual(result.facts, ())

    def test_ambiguous_message_has_no_amendment(self):
        result = parse_message(message("There may be an update to your account soon."), loaded())
        self.assertEqual(result.disposition, "no_action")
        self.assertEqual(result.facts, ())

    def test_multiple_facts_and_decimal_extraction(self):
        facts = self.fact(
            "Your salary is now EUR 1037.52 effective 2026-09-15. "
            "A recurring childcare payment of EUR 100.25 begins in the same month."
        )
        self.assertEqual(
            [fact.kind for fact in facts],
            ["salary_income", "salary_payment_date", "payment_change"],
        )
        self.assertEqual(facts[2].amount, Decimal("100.25"))
        self.assertIsInstance(facts[0].amount, Decimal)

    def test_explicit_override_and_no_implicit_newer_override(self):
        explicit = self.fact("The revised salary is USD 1200.00. This replaces the earlier amount.")
        self.assertEqual(explicit[0].action, "override")
        ordinary = self.fact("Your salary is USD 1200.00.")
        self.assertEqual(ordinary[0].action, "amend")


if __name__ == "__main__":
    unittest.main()
