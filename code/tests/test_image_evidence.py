from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from code.image_evidence import (
    CandidateEvidence,
    UnavailableExtractor,
    resolve_image_amount,
)


class StubExtractor:
    def __init__(self, candidates):
        self.candidates = candidates
        self.seen_path = None

    def extract(self, image_path, image_metadata, event):
        self.seen_path = image_path
        return self.candidates


def _event(**overrides):
    event = {
        "event_id": "dynamic-event",
        "user_id": "dynamic-user",
        "amount": None,
        "currency": "INR",
        "description": "medical payment",
        "category": "health",
    }
    event.update(overrides)
    return event


class ImageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.media = Path(self.directory.name)
        Image.new("RGB", (20, 20), "white").save(self.media / "receipt.png")
        self.records = [{
            "image_id": "receipt",
            "user_id": "dynamic-user",
            "related_event_id": "dynamic-event",
        }]

    def tearDown(self):
        self.directory.cleanup()

    def candidate(self, **overrides):
        values = {
            "amount": Decimal("123.45"),
            "currency": "INR",
            "document_type": "medical receipt",
            "amount_label": "payable",
            "confidence": Decimal("0.95"),
            "identity_text": "medical payment",
        }
        values.update(overrides)
        return CandidateEvidence(**values)

    def test_dynamic_path_and_actual_image_opening(self):
        extractor = StubExtractor((self.candidate(),))
        evidence = resolve_image_amount(_event(), self.records, self.media, extractor)
        self.assertEqual(evidence.amount, Decimal("123.45"))
        self.assertEqual(extractor.seen_path.name, "receipt.png")

    def test_missing_backend_is_unresolved(self):
        self.assertIsNone(resolve_image_amount(
            _event(), self.records, self.media, UnavailableExtractor()
        ))

    def test_malformed_extractor_output_is_unresolved(self):
        self.assertIsNone(resolve_image_amount(
            _event(), self.records, self.media, StubExtractor(({"amount": 1},))
        ))

    def test_valid_generic_candidate(self):
        evidence = resolve_image_amount(
            _event(), self.records, self.media, StubExtractor((self.candidate(),))
        )
        self.assertEqual(evidence.currency, "INR")

    def test_ambiguous_multiple_candidates_are_unresolved(self):
        candidates = (self.candidate(), self.candidate(amount=Decimal("456")))
        self.assertIsNone(resolve_image_amount(
            _event(), self.records, self.media, StubExtractor(candidates)
        ))

    def test_currency_mismatch_is_unresolved(self):
        self.assertIsNone(resolve_image_amount(
            _event(), self.records, self.media,
            StubExtractor((self.candidate(currency="USD"),)),
        ))

    def test_identity_conflict_is_unresolved(self):
        self.assertIsNone(resolve_image_amount(
            _event(), self.records, self.media,
            StubExtractor((self.candidate(identity_text="unrelated merchant"),)),
        ))

    def test_missing_image_is_unresolved(self):
        record = [{**self.records[0], "image_id": "missing"}]
        self.assertIsNone(resolve_image_amount(
            _event(), record, self.media, StubExtractor((self.candidate(),))
        ))

    def test_nonblank_structured_amount_is_never_overridden(self):
        self.assertIsNone(resolve_image_amount(
            _event(amount=Decimal("1")),
            self.records,
            self.media,
            StubExtractor((self.candidate(),)),
        ))


if __name__ == "__main__":
    unittest.main()
