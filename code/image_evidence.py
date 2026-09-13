"""Optional local image evidence extraction with deterministic validation."""

from __future__ import annotations

import json
import os
import re
import shutil
import shlex
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


@dataclass(frozen=True)
class CandidateEvidence:
    amount: Decimal
    currency: str
    document_type: str = ""
    amount_label: str = ""
    confidence: Decimal = Decimal("0")
    identity_text: str = ""
    conflict_indicators: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageAmountEvidence:
    image_id: str
    event_id: str
    amount: Decimal
    currency: str
    clarity: str


class ImageEvidenceExtractor(Protocol):
    """Extract candidate financial evidence without making a financial decision."""

    def extract(
        self,
        image_path: Path,
        image_metadata: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> Iterable[CandidateEvidence]:
        ...


class UnavailableExtractor:
    """Safe no-op extractor used when no supported backend is available."""

    def extract(self, image_path, image_metadata, event):
        return ()


_CURRENCY_PATTERN = re.compile(
    r"\b(AUD|CAD|CHF|EUR|GBP|IDR|INR|JPY|SGD|USD)\b|[$€£₹]"
)
_AMOUNT_PATTERN = re.compile(
    r"(?<![\d.])\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?(?![\d.])"
)
_SYMBOL_CURRENCIES = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_IDENTITY_WORDS = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)
_SUPPORTED_DOCUMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}


def _currency_from_match(match: re.Match[str]) -> str:
    value = match.group(0).upper()
    return _SYMBOL_CURRENCIES.get(value, value)


def _parse_ocr_text(text: str) -> tuple[CandidateEvidence, ...]:
    currencies = [_currency_from_match(match) for match in _CURRENCY_PATTERN.finditer(text)]
    if len(set(currencies)) != 1:
        return ()
    amounts: list[Decimal] = []
    for raw in _AMOUNT_PATTERN.findall(text):
        try:
            value = Decimal(raw.replace(",", "").replace(" ", ""))
        except InvalidOperation:
            continue
        if value > 0:
            amounts.append(value)
    if len(amounts) != 1:
        return ()
    return (
        CandidateEvidence(
            amount=amounts[0],
            currency=currencies[0],
            amount_label="",
            confidence=Decimal("0.70"),
        ),
    )


class TesseractOCRExtractor:
    """Use pytesseract only when both its package and executable are available."""

    def __init__(self, pytesseract_module: Any):
        self._pytesseract = pytesseract_module

    def extract(self, image_path, image_metadata, event):
        from PIL import Image

        with Image.open(image_path) as image:
            image.load()
            text = self._pytesseract.image_to_string(image)
        return _parse_ocr_text(text)


class CommandVLMExtractor:
    """Use an explicitly configured local command returning JSON candidates."""

    def __init__(self, command: str):
        self._command = tuple(shlex.split(command, posix=False))

    def extract(self, image_path, image_metadata, event):
        payload = json.dumps(
            {"image_metadata": dict(image_metadata), "event": dict(event)},
            default=str,
        )
        completed = subprocess.run(
            (*self._command, str(image_path), payload),
            capture_output=True,
            text=True,
            check=True,
        )
        raw = json.loads(completed.stdout)
        candidates = raw if isinstance(raw, list) else raw.get("candidates", [])
        result = []
        for item in candidates:
            if not isinstance(item, Mapping):
                continue
            try:
                result.append(
                    CandidateEvidence(
                        amount=Decimal(str(item["amount"])),
                        currency=str(item["currency"]).upper(),
                        document_type=str(item.get("document_type", "")),
                        amount_label=str(item.get("amount_label", "")),
                        confidence=Decimal(str(item.get("confidence", "0"))),
                        identity_text=str(item.get("identity_text", "")),
                        conflict_indicators=tuple(item.get("conflict_indicators", ())),
                    )
                )
            except (KeyError, InvalidOperation, TypeError, ValueError):
                continue
        return tuple(result)


def default_extractor() -> ImageEvidenceExtractor:
    """Select an available backend without downloading or contacting a service."""

    command = os.environ.get("BUDGY_VLM_COMMAND", "").strip()
    if command:
        return CommandVLMExtractor(command)
    if shutil.which("tesseract"):
        try:
            import pytesseract
        except ImportError:
            pass
        else:
            return TesseractOCRExtractor(pytesseract)
    return UnavailableExtractor()


def _image_path(media_root: Path, image_id: str) -> Path | None:
    matches = sorted(
        path for path in media_root.glob(f"{image_id}.*")
        if path.is_file() and path.suffix.lower() in _SUPPORTED_DOCUMENT_EXTENSIONS
    )
    return matches[0] if len(matches) == 1 else None


def _open_image(path: Path) -> None:
    from PIL import Image

    with Image.open(path) as image:
        image.verify()


def _identity_compatible(candidate: CandidateEvidence, event: Mapping[str, Any]) -> bool:
    if candidate.conflict_indicators:
        return False
    if not candidate.identity_text:
        return False
    event_words = set(_IDENTITY_WORDS.findall(
        f"{event.get('description', '')} {event.get('category', '')}".lower()
    ))
    identity_words = set(_IDENTITY_WORDS.findall(candidate.identity_text.lower()))
    return not identity_words or bool(event_words & identity_words)


def _validate_candidate(
    candidate: CandidateEvidence,
    event: Mapping[str, Any],
) -> bool:
    return (
        isinstance(candidate, CandidateEvidence)
        and isinstance(candidate.amount, Decimal)
        and candidate.amount > 0
        and candidate.currency == event.get("currency")
        and Decimal("0") <= candidate.confidence <= Decimal("1")
        and candidate.confidence >= Decimal("0.70")
        and _identity_compatible(candidate, event)
    )


def resolve_image_amount(
    event: Mapping[str, Any],
    image_records: Iterable[Mapping[str, Any]] = (),
    media_root: str | Path | None = None,
    extractor: ImageEvidenceExtractor | None = None,
) -> ImageAmountEvidence | None:
    """Resolve one validated amount from one dynamically linked local image."""

    if event.get("amount") is not None or media_root is None:
        return None
    linked = [
        row for row in image_records
        if row.get("related_event_id") == event.get("event_id")
        and row.get("user_id") == event.get("user_id")
    ]
    if len(linked) != 1:
        return None
    image_id = str(linked[0].get("image_id", ""))
    path = _image_path(Path(media_root), image_id)
    if path is None:
        return None
    try:
        _open_image(path)
        candidates = tuple((extractor or default_extractor()).extract(path, linked[0], event))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if len(candidates) != 1 or not _validate_candidate(candidates[0], event):
        return None
    candidate = candidates[0]
    return ImageAmountEvidence(
        image_id=image_id,
        event_id=str(event["event_id"]),
        amount=candidate.amount,
        currency=candidate.currency,
        clarity=f"{candidate.document_type}:{candidate.amount_label}".strip(":"),
    )
