"""Deterministic parsing of explicit financial facts in untrusted messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping

from code.loaders import LoadedData


class MessageParsingError(ValueError):
    """Raised when a message cannot be structurally parsed."""


@dataclass(frozen=True)
class MessageFact:
    """One independently validated financial fact extracted from a message."""

    kind: str
    status: str
    action: str
    spendable: bool
    amount: Decimal | None = None
    currency: str | None = None
    effective_date: date | None = None
    percentage: Decimal | None = None
    event_id: str | None = None
    description: str = ""


@dataclass(frozen=True)
class ParsedMessage:
    """A parsed message and its safe-to-consume structured facts."""

    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: datetime | None
    source_type: str
    facts: tuple[MessageFact, ...]
    disposition: str
    ambiguity_reason: str | None = None


_CURRENCY = r"(IDR|USD|EUR|GBP|INR|ZAR|SGD|AUD|CAD|JPY|MYR)"
_MONEY = re.compile(rf"\b{_CURRENCY}\s*([0-9][0-9.,]*)\b", re.I)
_DATE = re.compile(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b")
_OVERRIDE = re.compile(
    r"\b(replaces?|revised|updated|new amount|use the revised|menggantikan|"
    r"diperbarui|jumlah baru|berubah menjadi|naik menjadi|turun menjadi)\b",
    re.I,
)


def _money(text: str, start: int = 0) -> tuple[Decimal, str] | None:
    match = _MONEY.search(text, start)
    if not match:
        return None
    raw = match.group(2).replace(",", "")
    try:
        return Decimal(raw), match.group(1).upper()
    except InvalidOperation:
        return None


def _date(text: str, start: int = 0) -> date | None:
    match = _DATE.search(text, start)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _percentage(text: str) -> Decimal | None:
    match = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\s*%", text)
    return Decimal(match.group(1)) if match else None


def _ids(loaded: LoadedData, filename: str, key: str) -> set[str]:
    return {str(row[key]) for row in loaded.tables.get(filename, []) if row.get(key)}


def _validate_context(row: Mapping[str, Any], loaded: LoadedData) -> str | None:
    user_id = row.get("user_id")
    if user_id and _ids(loaded, "financial_profiles.csv", "user_id"):
        if user_id not in _ids(loaded, "financial_profiles.csv", "user_id"):
            return f"unknown user_id: {user_id}"
    request_id = row.get("request_id")
    if request_id and _ids(loaded, "requests.csv", "request_id"):
        sample_ids = _ids(loaded, "sample_requests.csv", "request_id")
        if request_id not in _ids(loaded, "requests.csv", "request_id") and request_id not in sample_ids:
            return f"unknown request_id: {request_id}"
    event_id = row.get("related_event_id")
    if event_id and event_id not in _ids(loaded, "financial_events.csv", "event_id"):
        return f"unknown related_event_id: {event_id}"
    return None


def _fact(
    kind: str,
    status: str,
    action: str,
    spendable: bool,
    row: Mapping[str, Any],
    **kwargs: Any,
) -> MessageFact:
    return MessageFact(
        kind=kind,
        status=status,
        action=action,
        spendable=spendable,
        event_id=row.get("related_event_id"),
        **kwargs,
    )


def parse_message(
    message: Mapping[str, Any],
    loaded_data: LoadedData,
    *,
    llm_fallback: Callable[[Mapping[str, Any]], Iterable[MessageFact]] | None = None,
) -> ParsedMessage:
    """Parse one message without guessing unsupported financial facts."""

    required = ("message_id", "user_id", "source_type", "message_text")
    missing = [field for field in required if not message.get(field)]
    if missing:
        raise MessageParsingError(f"Message is missing required fields: {', '.join(missing)}")

    context_error = _validate_context(message, loaded_data)
    base = dict(
        message_id=message["message_id"],
        user_id=message["user_id"],
        request_id=message.get("request_id"),
        related_event_id=message.get("related_event_id"),
        sent_at=message.get("sent_at"),
        source_type=message["source_type"],
    )
    if context_error:
        return ParsedMessage(**base, facts=(), disposition="ambiguous", ambiguity_reason=context_error)

    text = str(message["message_text"])
    lower = text.lower()
    facts: list[MessageFact] = []
    linked_event = bool(message.get("related_event_id"))
    override = bool(_OVERRIDE.search(text))

    salary_terms = r"(salary|payroll|gaji|pendapatan|penghasilan|gaji pokok)"
    salary_money = _money(text)
    if salary_money and re.search(salary_terms, lower):
        amount, currency = salary_money
        facts.append(_fact(
            "salary_income", "confirmed", "override" if override else "amend",
            True, message, amount=amount, currency=currency,
            effective_date=_date(text, _MONEY.search(text).end()),
            description="Explicit salary or income amount",
        ))

    salary_date = _date(text)
    if salary_date and re.search(r"(salary|payroll|pay|gaji|penggajian|credit date|tanggal kredit)", lower):
        facts.append(_fact(
            "salary_payment_date", "confirmed", "override" if override else "amend",
            False, message, effective_date=salary_date,
            description="Explicit salary or payroll date",
        ))

    ended = re.search(
        r"(employment has ended|employment record has ended|contract .* ended|"
        r"seasonal contract has ended|kontrak .* berakhir|pekerjaan .* berakhir|"
        r"no regular salary|no off-season income)",
        lower,
    )
    if ended:
        facts.append(_fact(
            "employment_ended", "confirmed", "amend", False, message,
            effective_date=_date(text),
            description="Future income ends; no replacement income inferred",
        ))

    if re.search(r"(refund|pengembalian)", lower):
        completed = bool(re.search(
            r"(refund .* (?:completed|credited)|refund is complete|"
            r"pengembalian .* (?:masuk|diterima|selesai))", lower
        )) and not bool(re.search(
            r"(refund .* (?:has not|hasn't|not) reached|refund .* still processing|"
            r"pengembalian .* belum masuk)", lower
        ))
        facts.append(_fact(
            "refund", "completed" if completed else "pending",
            "confirm" if completed else "amend", completed, message,
            amount=salary_money[0] if completed and salary_money else None,
            currency=salary_money[1] if completed and salary_money else None,
            description="Refund is credited only when explicitly completed",
        ))

    if re.search(r"(previous debit attempt failed|debit sebelumnya gagal)", lower):
        facts.append(_fact(
            "failed_debit", "failed", "amend", False, message,
            description="Failed debit remains an outstanding obligation",
        ))

    if re.search(r"(dispute|sengketa)", lower):
        facts.append(_fact(
            "dispute", "open", "amend", False, message,
            description="No reversal is spendable until posted",
        ))

    if re.search(r"(transfer between your two accounts|transfer antar rekening|same account holder|pemegang rekening yang sama)", lower):
        facts.append(_fact(
            "internal_transfer", "confirmed", "exclude", False, message,
            description="Internal transfer does not change spendable cash",
        ))

    unrealized = re.search(
        r"(no units have been sold|no cash proceeds|not been sold|no cash transaction|"
        r"belum dijual|tidak ada transaksi tunai|belum direalisasikan)",
        lower,
    )
    if unrealized and re.search(
        r"(investment|portfolio|investasi|saham|nilai investasi|units|unit|cash proceeds)",
        lower,
    ):
        facts.append(_fact(
            "investment_valuation", "unrealized", "exclude", False, message,
            description="Displayed investment value is not cash",
        ))
    elif re.search(r"(investment sale|sale proceeds|hasil penjualan investasi|penjualan investasi)", lower) and re.search(
        r"(settled|cash account|masuk ke rekening tunai|sudah masuk)", lower
    ):
        amount_currency = _money(text)
        facts.append(_fact(
            "investment_sale", "settled", "confirm", True, message,
            amount=amount_currency[0] if amount_currency else None,
            currency=amount_currency[1] if amount_currency else None,
            description="Investment-sale proceeds explicitly settled",
        ))

    payment_terms = r"(rent|sewa|payment|pembayaran|tagihan|invoice|faktur|childcare|subscription|langganan)"
    if re.search(payment_terms, lower):
        all_money = list(_MONEY.finditer(text))
        payment_money = None
        if len(all_money) > 1:
            match = all_money[1]
            payment_money = (Decimal(match.group(2).replace(",", "")), match.group(1).upper())
        if payment_money and not re.search(r"(salary|payroll|gaji|pendapatan|penghasilan)", lower):
            facts.append(_fact(
                "payment_change", "confirmed", "override" if override else "amend",
                False, message, amount=payment_money[0], currency=payment_money[1],
                effective_date=_date(text, all_money[1].end()),
                percentage=_percentage(text),
                description="Explicit future payment or obligation change",
            ))
        elif payment_money and re.search(r"(childcare|subscription|langganan)", lower):
            facts.append(_fact(
                "payment_change", "confirmed", "amend", False, message,
                amount=payment_money[0], currency=payment_money[1],
                effective_date=_date(text, all_money[1].end()),
                percentage=_percentage(text),
                description="Explicit future payment or obligation change",
            ))
        elif _percentage(text) is not None:
            facts.append(_fact(
                "payment_change", "confirmed", "amend", False, message,
                percentage=_percentage(text), effective_date=_date(text),
                description="Explicit percentage payment change",
            ))

    uncertain_income = re.search(
        r"(bonus|commission|payout|earnings|income|pendapatan|penghasilan|"
        r"bonus|komisi).{0,120}(pending|not approved|not confirmed|can change|"
        r"belum disetujui|belum dikonfirmasi|belum final|masih menunggu|"
        r"tidak masuk pembayaran|not withdrawable)",
        lower,
    )
    if uncertain_income:
        facts.append(_fact(
            "unconfirmed_income", "pending", "exclude", False, message,
            description="Unapproved or non-withdrawable income is excluded",
        ))

    reimbursement = re.search(
        r"(reimbursement|reimbursed|penggantian atas biaya kerja|penggantian biaya kerja)",
        lower,
    )
    if reimbursement and re.search(r"(closed|credited|latest employer credit|klaim sudah ditutup)", lower):
        amount_currency = _money(text)
        facts.append(_fact(
            "employer_reimbursement", "completed", "confirm", True, message,
            amount=amount_currency[0] if amount_currency else None,
            currency=amount_currency[1] if amount_currency else None,
            description="Confirmed employer reimbursement",
        ))

    if not facts and llm_fallback is not None:
        facts = list(llm_fallback(message))
    disposition = "parsed" if facts else "no_action"
    return ParsedMessage(**base, facts=tuple(facts), disposition=disposition)


def parse_messages(
    messages: Iterable[Mapping[str, Any]], loaded_data: LoadedData
) -> list[ParsedMessage]:
    """Parse messages in source order."""

    return [parse_message(message, loaded_data) for message in messages]
