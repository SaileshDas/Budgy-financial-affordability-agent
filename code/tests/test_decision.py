"""Focused tests for deterministic Decision Stage 1."""

from datetime import date, timedelta
from decimal import Decimal
import unittest

from code.decision import (
    ValidatedPaymentPlan,
    _simulate_plan,
    affordability_status,
    amount_safe_to_pay,
    earliest_date_for_full_payment,
    evaluate_stage2_decision,
    validate_payment_options,
)
from code.forecast import build_baseline_forecast
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


def event(event_id, settlement_date, **overrides):
    row = {
        "event_id": event_id,
        "user_id": "user_1",
        "event_type": "expense",
        "category": "rent",
        "description": "test",
        "direction": "debit",
        "amount": Decimal("100.00"),
        "currency": "USD",
        "event_date": settlement_date,
        "settlement_date": settlement_date,
        "status": "settled",
        "linked_event_id": None,
        "flexibility": "fixed",
        "minimum_allowed_amount": None,
    }
    row.update(overrides)
    return row


def forecast(events=None, **profile_overrides):
    return build_baseline_forecast(
        LoadedData(
            {
                "financial_profiles.csv": [profile(**profile_overrides)],
                "financial_events.csv": list(events or []),
                "exchange_rates.csv": [],
            }
        ),
        "user_1",
        REQUEST_DATE,
    )


def request(**overrides):
    row = {
        "request_id": "request_1",
        "user_id": "user_1",
        "request_date": REQUEST_DATE,
        "requested_amount": Decimal("900"),
        "desired_completion_date": REQUEST_DATE + timedelta(days=60),
        "allows_partial_payment": True,
        "request_text": "test",
    }
    row.update(overrides)
    return row


def option(**overrides):
    row = {
        "payment_option_id": "option_1",
        "request_id": "request_1",
        "payment_method": "installments",
        "payment_amount": Decimal("300"),
        "number_of_payments": 3,
        "first_payment_date": REQUEST_DATE,
        "payment_frequency_days": 30,
        "financing_fee": Decimal("0"),
        "total_payable_amount": Decimal("900"),
    }
    row.update(overrides)
    return row


def stage2_data(events=None, options=None, **profile_overrides):
    row = profile(payment_methods_user_will_consider="full_payment|installments|partial_payment",
                 max_installment_months=6, **profile_overrides)
    return LoadedData({
        "financial_profiles.csv": [row],
        "financial_events.csv": list(events or []),
        "exchange_rates.csv": [],
        "request_payment_options.csv": list(options or []),
        "messages.csv": [],
    })


class DecisionStage1Tests(unittest.TestCase):
    def test_request_fully_affordable_today(self):
        result = forecast()
        self.assertEqual(amount_safe_to_pay(result, Decimal("500")), Decimal("500"))
        self.assertEqual(affordability_status(result, Decimal("500")), "affordable_now")
        self.assertEqual(
            earliest_date_for_full_payment(result, Decimal("500")),
            REQUEST_DATE,
        )

    def test_request_partially_affordable_today(self):
        result = forecast()
        self.assertEqual(amount_safe_to_pay(result, Decimal("900")), Decimal("800.00"))
        self.assertEqual(affordability_status(result, Decimal("900")), "not_affordable")

    def test_request_affordable_after_future_income(self):
        result = forecast([
            event(
                "income",
                REQUEST_DATE + timedelta(days=5),
                event_type="income",
                category="salary",
                direction="credit",
                amount=Decimal("500"),
            )
        ])
        self.assertEqual(
            earliest_date_for_full_payment(result, Decimal("1200")),
            REQUEST_DATE + timedelta(days=5),
        )
        self.assertEqual(
            affordability_status(result, Decimal("1200")),
            "affordable_later",
        )

    def test_request_not_affordable(self):
        result = forecast([event("future", REQUEST_DATE + timedelta(days=1), amount=Decimal("900"))])
        self.assertEqual(amount_safe_to_pay(result, Decimal("100")), Decimal("0"))
        self.assertEqual(affordability_status(result, Decimal("100")), "not_affordable")
        self.assertIsNone(earliest_date_for_full_payment(result, Decimal("100")))

    def test_minimum_balance_protection_and_requested_cap(self):
        result = forecast()
        self.assertEqual(amount_safe_to_pay(result, Decimal("9999")), Decimal("800.00"))
        self.assertLessEqual(
            result.starting_balance - amount_safe_to_pay(result, Decimal("9999")),
            result.starting_balance,
        )

    def test_zero_safe_amount_when_no_surplus_exists(self):
        result = forecast(current_available_balance=Decimal("200.00"))
        self.assertEqual(amount_safe_to_pay(result, Decimal("1")), Decimal("0"))

    def test_future_expenses_reduce_available_amount(self):
        result = forecast([event("expense", REQUEST_DATE + timedelta(days=3), amount=Decimal("250"))])
        self.assertEqual(amount_safe_to_pay(result, Decimal("1000")), Decimal("550.00"))

    def test_future_income_increases_safe_amount(self):
        result = forecast([event(
            "income",
            REQUEST_DATE + timedelta(days=3),
            event_type="income",
            category="salary",
            direction="credit",
            amount=Decimal("250"),
        )])
        self.assertEqual(amount_safe_to_pay(result, Decimal("1000")), Decimal("800.00"))

    def test_earliest_date_and_deadline(self):
        income_date = REQUEST_DATE + timedelta(days=10)
        result = forecast([event(
            "income",
            income_date,
            event_type="income",
            category="salary",
            direction="credit",
            amount=Decimal("300"),
        )])
        self.assertEqual(
            earliest_date_for_full_payment(result, Decimal("1000"), income_date),
            income_date,
        )
        self.assertIsNone(
            earliest_date_for_full_payment(
                result, Decimal("1000"), REQUEST_DATE + timedelta(days=5)
            )
        )

    def test_decimal_precision(self):
        result = forecast(current_available_balance=Decimal("1000.01"))
        safe = amount_safe_to_pay(result, Decimal("800.009"))
        self.assertEqual(safe, Decimal("800.009"))
        self.assertIsInstance(safe, Decimal)


class DecisionStage2Tests(unittest.TestCase):
    def test_simulation_applies_payments_only_on_their_dates(self):
        result = forecast([
            event("expense", REQUEST_DATE + timedelta(days=3), amount=Decimal("700")),
        ])
        plan = ValidatedPaymentPlan(
            "dated", "installments",
            ((REQUEST_DATE + timedelta(days=5), Decimal("300")),),
            Decimal("300"), 1, Decimal("0"), Decimal("300"),
        )
        self.assertFalse(_simulate_plan(result, plan))

    def test_later_expense_can_make_later_installment_unsafe(self):
        result = forecast([
            event("expense", REQUEST_DATE + timedelta(days=7), amount=Decimal("600")),
        ])
        plan = ValidatedPaymentPlan(
            "dated", "installments",
            (
                (REQUEST_DATE + timedelta(days=5), Decimal("300")),
                (REQUEST_DATE + timedelta(days=10), Decimal("300")),
            ),
            Decimal("300"), 2, Decimal("0"), Decimal("600"),
        )
        self.assertFalse(_simulate_plan(result, plan))

    def test_future_income_between_installments_preserves_later_payment(self):
        result = forecast([
            event(
                "income", REQUEST_DATE + timedelta(days=7),
                event_type="income", category="salary", direction="credit",
                amount=Decimal("300"),
            ),
        ], current_available_balance=Decimal("500"), minimum_balance_to_keep=Decimal("200"))
        plan = ValidatedPaymentPlan(
            "dated", "installments",
            (
                (REQUEST_DATE + timedelta(days=5), Decimal("100")),
                (REQUEST_DATE + timedelta(days=10), Decimal("200")),
            ),
            Decimal("100"), 2, Decimal("0"), Decimal("300"),
        )
        self.assertTrue(_simulate_plan(result, plan))

    def test_valid_full_and_installment_options_preserve_supplied_amount(self):
        req = request()
        data = stage2_data(options=[
            option(payment_option_id="full", payment_method="full_payment",
                   payment_amount=Decimal("900"), number_of_payments=1,
                   first_payment_date=REQUEST_DATE, payment_frequency_days=None,
                   total_payable_amount=Decimal("900")),
            option(),
        ])
        plans = validate_payment_options(data, req)
        self.assertEqual({plan.payment_option_id for plan in plans}, {"full", "option_1"})
        installment = next(plan for plan in plans if plan.payment_option_id == "option_1")
        self.assertEqual(installment.payment_amount, Decimal("300"))
        self.assertEqual(installment.payments[1][0], date(2026, 1, 31))

    def test_invalid_options_deadline_and_max_months_are_rejected(self):
        req = request(desired_completion_date=REQUEST_DATE + timedelta(days=20))
        data = stage2_data(options=[
            option(payment_option_id="late", number_of_payments=2,
                   payment_frequency_days=30),
            option(payment_option_id="too_many", number_of_payments=7),
            option(payment_option_id="missing", payment_amount=None),
        ])
        self.assertEqual(validate_payment_options(data, req), ())

    def test_installment_succeeds_and_selected_method_is_installments(self):
        req = request(requested_amount=Decimal("600"))
        data = stage2_data(options=[option(payment_amount=Decimal("200"),
                                           number_of_payments=3,
                                           total_payable_amount=Decimal("600"))])
        result = evaluate_stage2_decision(data, req)
        self.assertEqual(result.affordability_status, "affordable_now")
        self.assertEqual(result.recommended_payment_method, "installments")
        self.assertEqual(result.payment_plan, "2026-01-01:200|2026-01-31:200|2026-03-02:200")

    def test_later_expense_can_make_installment_plan_fail(self):
        req = request(requested_amount=Decimal("600"))
        data = stage2_data(
            [event("expense", date(2026, 2, 1), amount=Decimal("850"))],
            [option(payment_amount=Decimal("200"), number_of_payments=3,
                    total_payable_amount=Decimal("600"))],
        )
        result = evaluate_stage2_decision(data, req)
        self.assertEqual(result.recommended_payment_method, "not_recommended")

    def test_future_income_can_make_plan_feasible(self):
        req = request(requested_amount=Decimal("1200"), allows_partial_payment=False)
        data = stage2_data(
            [event("income", date(2026, 1, 10), event_type="income",
                   category="salary", direction="credit", amount=Decimal("500"))],
            [option(payment_amount=Decimal("400"), number_of_payments=3,
                    total_payable_amount=Decimal("1200"))],
        )
        result = evaluate_stage2_decision(data, req)
        self.assertEqual(result.affordability_status, "affordable_with_plan")
        self.assertEqual(result.recommended_payment_method, "installments")

    def test_spending_scenario_does_not_change_baseline(self):
        recurring = [
            event("x1", date(2025, 10, 1), category="streaming", flexibility="stoppable", amount=Decimal("400")),
            event("x2", date(2025, 11, 1), category="streaming", flexibility="stoppable", amount=Decimal("400")),
            event("x3", date(2025, 12, 1), category="streaming", flexibility="stoppable", amount=Decimal("400")),
        ]
        req = request(requested_amount=Decimal("600"))
        data = stage2_data(recurring, [option(payment_amount=Decimal("200"),
                                              number_of_payments=3,
                                              total_payable_amount=Decimal("600"))],
                           expense_categories_user_is_willing_to_stop="streaming")
        result = evaluate_stage2_decision(data, req)
        self.assertEqual(result.baseline_result.amount_safe_to_pay, Decimal("0"))
        self.assertEqual(result.spending_changes_needed, "stop:x1")

    def test_statuses_and_safe_amount_semantics(self):
        now = evaluate_stage2_decision(
            stage2_data(options=[option(payment_method="full_payment",
                                         payment_amount=Decimal("500"),
                                         number_of_payments=1,
                                         payment_frequency_days=None,
                                         total_payable_amount=Decimal("500"))]),
            request(requested_amount=Decimal("500")),
        )
        self.assertEqual(now.affordability_status, "affordable_now")
        self.assertEqual(now.earliest_date_for_full_payment, REQUEST_DATE)
        self.assertLessEqual(now.amount_safe_to_pay, Decimal("900"))


if __name__ == "__main__":
    unittest.main()
