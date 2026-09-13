"""Tests for dataset loading and input normalization."""

from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from code.loaders import (
    REQUIRED_COLUMNS,
    REQUIRED_FILES,
    MissingColumnError,
    MissingInputFileError,
    load_all,
)


class LoaderTests(unittest.TestCase):
    def _fixture_dir(self) -> Path:
        directory = Path(tempfile.mkdtemp())
        for filename in REQUIRED_FILES:
            columns = list(REQUIRED_COLUMNS[filename])
            with (directory / filename).open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(columns)
        return directory

    def test_successful_loading(self) -> None:
        loaded = load_all()
        self.assertEqual(len(loaded["requests.csv"]), 250)
        self.assertEqual(len(loaded["financial_profiles.csv"]), 275)
        self.assertIsInstance(loaded["requests.csv"][0]["requested_amount"], Decimal)
        self.assertIsInstance(loaded["requests.csv"][0]["request_date"], date)

    def test_output_csv_is_not_required_input(self) -> None:
        directory = self._fixture_dir()
        loaded = load_all(directory)
        self.assertIn("requests.csv", loaded.tables)

    def test_missing_file(self) -> None:
        directory = self._fixture_dir()
        (directory / "requests.csv").unlink()
        with self.assertRaises(MissingInputFileError):
            load_all(directory)

    def test_missing_required_column(self) -> None:
        directory = self._fixture_dir()
        with (directory / "requests.csv").open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(["request_id"])
        with self.assertRaises(MissingColumnError):
            load_all(directory)

    def test_blank_event_amount_remains_missing(self) -> None:
        directory = self._fixture_dir()
        event = [
            "event_1", "user_1", "expense", "Test", "rent", "debit", "",
            "USD", "2026-01-01", "2026-01-02", "settled", "", "fixed", "",
        ]
        with (directory / "financial_events.csv").open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(event)
        loaded = load_all(directory)
        self.assertIsNone(loaded["financial_events.csv"][0]["amount"])

    def test_date_parsing_and_normalization(self) -> None:
        directory = self._fixture_dir()
        request = [
            "request_1", "user_1", "2026: 01- 02", "purchase", "12.50",
            "2026-02-03", "false", "Test request",
        ]
        with (directory / "requests.csv").open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(request)
        loaded = load_all(directory)
        row = loaded["requests.csv"][0]
        self.assertEqual(row["request_date"], date(2026, 1, 2))
        self.assertEqual(row["desired_completion_date"], date(2026, 2, 3))
        self.assertFalse(row["allows_partial_payment"])


if __name__ == "__main__":
    unittest.main()
