from datetime import date
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from code.output import (
    OUTPUT_COLUMNS,
    blocked_row,
    format_date,
    format_money,
    validate_output_rows,
    write_output,
)


def request(request_id="request_1"):
    return {
        "request_id": request_id,
        "request_date": date(2026, 1, 1),
        "desired_completion_date": date(2026, 2, 1),
        "requested_amount": Decimal("100.00"),
    }


def safe_row(request_id="request_1"):
    return {
        "request_id": request_id,
        "amount_safe_to_pay": "50.00",
        "affordability_status": "affordable_later",
        "recommended_payment_method": "wait",
        "payment_plan": "none",
        "earliest_date_for_full_payment": "2026-01-10",
        "spending_changes_needed": "none",
        "decision_explanation": "Wait.",
    }


class OutputTests(unittest.TestCase):
    def test_formatting_and_column_order(self):
        self.assertEqual(format_money(Decimal("1.005")), "1.01")
        self.assertEqual(format_date(date(2026, 1, 2)), "2026-01-02")
        self.assertEqual(tuple(safe_row()), OUTPUT_COLUMNS)

    def test_blocked_row_is_explicit_and_valid(self):
        validate_output_rows([blocked_row("request_1", ValueError("missing"))], [request()])

    def test_validator_rejects_invalid_status_method_and_amount(self):
        for field, value in (
            ("affordability_status", "invalid"),
            ("recommended_payment_method", "invalid"),
            ("amount_safe_to_pay", "101.00"),
        ):
            row = safe_row()
            row[field] = value
            with self.assertRaises(ValueError):
                validate_output_rows([row], [request()])

    def test_affordable_now_requires_full_payment(self):
        row = safe_row()
        row["affordability_status"] = "affordable_now"
        row["recommended_payment_method"] = "installments"
        row["earliest_date_for_full_payment"] = "2026-01-01"
        with self.assertRaises(ValueError):
            validate_output_rows([row], [request()])

    def test_validator_rejects_duplicate_and_missing_ids(self):
        with self.assertRaises(ValueError):
            validate_output_rows([safe_row(), safe_row()], [request()])
        with self.assertRaises(ValueError):
            validate_output_rows([safe_row("other")], [request()])

    def test_writer_preserves_required_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.csv"
            write_output([safe_row()], path)
            self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0], ",".join(OUTPUT_COLUMNS))


if __name__ == "__main__":
    unittest.main()
