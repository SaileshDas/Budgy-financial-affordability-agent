# Implementation Decisions

| Area | Decision |
|---|---|
| Financial decisions | Deterministic Python is responsible for safety calculations and recommendations. |
| LLM usage | Optional; an LLM must not control financial safety decisions. |
| Cash timing | `settlement_date` determines cash timing. |
| Missing amounts | Missing event amounts are unresolved values, never zero. |
| Starting balance | Use the user's financial profile balance at the request date. |
| Safe amount | Calculate `amount_safe_to_pay` without optional spending changes. |
| Exchange rates | Use exact dated direct exchange rates only. |
| Spending changes | Require both event flexibility and user permission. |
| Payment plans | Follow supplied payment-plan dates and amounts exactly. |
| Tabular processing | Use pandas for CSV ingestion, joins, filtering, grouping, and related tabular work. |
| Financial arithmetic | Convert monetary values to `Decimal` at the financial-calculation boundary; do not use float arithmetic for decisions. |
| Event normalization | Represent financial events as normalized cash-flow records while preserving the source event fields. |
| Event cash date | Use `settlement_date`, not `event_date`, for cash-impact timing. |
| Event status handling | Settled debits and credits impact cash; pending/scheduled debits are future obligations; failed, cancelled, unrealized, and `non_cash` events do not impact cash. |
| Pending credits | Exclude pending and scheduled credits conservatively until settled. |
| Unresolved event amounts | Preserve missing amounts as unresolved and never interpret them as zero. |
| Currency lookup | Require one exact direct exchange rate matching settlement date, source currency, and home currency; do not use inverse, cross-currency, or nearest-date fallback. |
| Same-currency conversion | Return the original Decimal amount without an exchange-rate lookup when source and home currencies match. |
| Conversion failures | Raise an error for missing or invalid exchange-rate data rather than silently producing a value. |
| Converted cash flows | Preserve the original normalized cash flow and add its signed converted home-currency amount. |
| Message parsing | Use deterministic parsing for explicit, validated financial facts; use an optional LLM fallback only for ambiguous messages. |
| Message safety | An LLM must not control financial safety decisions, invent amounts or dates, or turn unconfirmed text into spendable cash. |
| Message confirmations | Use explicit settled/received/credited language to confirm an existing event. |
| Message amendments | Apply explicit salary, payment-date, rent, deduction, refund-state, and failed/disputed-status amendments only after validating identifiers, dates, currencies, and amounts. |
| Message overrides | Treat a message as an override only when it explicitly says an earlier amount or date is replaced or revised. |
| Unconfirmed messages | Exclude pending, unapproved, expected-but-not-credited, disputed-without-reversal, and unrealized-investment notices from spendable cash forecasts. |
| Internal transfers | Matching debit and credit messages identified as transfers between accounts owned by the same user do not change spendable cash. |
| Message links | Prefer `related_event_id` for event amendments; use `user_id`, `request_id`, source, timestamp, and explicit reference text only as validated context, not as implicit event identity. |
| Message ambiguity | Mixed-fact, contradictory, malformed, or untrusted messages are not allowed to silently alter the forecast; they require conservative exclusion or explicit review. |
| Parsed message structure | Represent each message as validated `MessageFact` records inside a `ParsedMessage`; keep parsing separate from forecasting and decisions. |
| Supported message facts | Parse explicit salary/income, pay-date, employment-ending, refund, failed-debit, dispute, internal-transfer, investment, payment-change, unconfirmed-income, and employer-reimbursement facts. |
| Multilingual parsing | Support the explicit English and Indonesian vocabulary found in the dataset; unsupported wording remains no-action. |
| Event association validation | Use `related_event_id` as the strongest association only after validating it against `financial_events.csv`; never infer an event association from message text alone. |
| Context identifiers | Validate `user_id` and `request_id` and use them only as context, not as implicit event identity. |
| Explicit overrides | Require replacement or revision language before marking a fact as an override; a newer message does not automatically replace an earlier fact. |
| Multiple message facts | Emit multiple facts when each independently contains explicit, validated financial information. |
| Message fallback | Keep an injectable LLM fallback extension point, but do not call an LLM until deterministic parsing cannot safely interpret a message. |
| Message no-action behavior | Unsupported, ambiguous, or insufficiently supported messages produce `no_action` and no financial amendment. |
| Experimental LLM benchmark | Benchmark only the deterministic `no_action` subset; never send all messages and never connect benchmark output to forecasting, affordability, or `output.csv`. |
| LLM benchmark result | No provider was configured, so the 31-message experiment made zero calls and found zero validated additional facts. Keep deterministic-only for now; the result is inconclusive about model quality. |
| LLM trust boundary | LLM output is candidate interpretation only and is never trusted directly for financial decisions. Any future candidate requires deterministic validation of identifiers, amounts, currencies, dates, status, and safety rules. |
| Forecast starting point | For each request, start from `financial_profiles.current_available_balance` and evaluate all future dates relative to that request's `request_date`. |
| Forecast cash flows | Consume normalized events, use `settlement_date` as cash date, convert through exact direct dated rates, and apply only validated message amendments. |
| Forecast status safety | Include settled credits/debits and future debit obligations; exclude pending/scheduled credits, failed, cancelled, unrealized, and non-cash records. Never treat unresolved amounts as zero. |
| Recurrence evidence | Infer recurrence only per user from repeated compatible events with stable category, direction, currency, flexibility, amount, and consistent interval. Do not project isolated or irregular events. |
| Recurring flexibility | Preserve `fixed`, `reducible`, `stoppable`, and `reducible_or_stoppable` on recurring templates for later scenario generation. |
| Forecast horizon | Use a minimum 90-day horizon from the request date and extend it through a later requested completion deadline. |
| Baseline versus scenarios | Build the baseline without optional spending changes. Model permitted spending changes as separate scenarios; do not let them alter baseline safe amount or earliest full-payment capacity. |
| Spending-change safety | Scenarios may change only recurring flexible events for which the user has permission. Stopping and reducing the same event are mutually exclusive. |
| Forecast API | Support baseline/scenario construction, balance on a date, minimum projected balance, and available surplus above the required minimum balance. |
| Forecast arithmetic | Use Decimal throughout; compare exact values and quantize to two decimals with `ROUND_HALF_UP` only at output/payment boundaries. |
| Forecast unresolved data | A cash-impacting event with an unresolved amount blocks reliable inclusion until image resolution; it is never silently imputed. |
| Forecast Stage 1 scope | Implement baseline daily cash-flow forecasting only; defer recurrence, message amendments, spending-change scenarios, payment plans, and affordability decisions. |
| Daily forecast records | Preserve opening balance, inflows, outflows, closing balance, minimum required balance, and converted source flows for each calendar date. |
| Forecast event window | Include cash-impacting events whose settlement dates fall from the request date through the forecast end date, inclusive. |
| Same-day ordering | Apply all cash flows dated on the same day together after that day's opening balance; the daily closing balance is the downstream decision-layer value. |
| Forecast safety propagation | Propagate `UnresolvedAmountError` for unresolved cash-impacting amounts so downstream decisions can handle the forecast as unsafe or incomplete. |
| Recurrence inference | Require at least three compatible settled observations with stable amounts and a weekly, biweekly, or monthly interval; prefer missing a weak recurrence over inventing future cash flow. |
| Recurrence exclusions | Do not infer recurrence from one-off, irregular, failed, cancelled, transfer, refund, investment, unresolved, or unconfirmed records. |
| Message amendments in forecast | Apply only parser-validated explicit facts with supported effective dates and existing event links where required; salary changes affect future salary flows and dated employment endings stop them. |
| Conservative message cash | Pending refunds/income, open disputes, internal transfers, and unrealized investments never create spendable forecast cash; completed linked refunds may create a settled credit. |
| Scenario isolation | Validate stop/reduce requests against recurring flexibility, profile permissions, and minimum amounts, then build a separate scenario without changing the baseline. |
| Forecast Stage 2 validation | Recurrence, amendments, exclusions, scenario rules, Decimal precision, and baseline isolation are covered; the complete suite passed 60/60. |
| Decision Stage 1 safe amount | Calculate capacity from the minimum baseline closing balance over the forecast horizon, subtracting the required minimum balance and capping at the requested amount. Optional spending changes are not assumed. |
| Decision Stage 1 earliest payment | A candidate full-payment date is valid only if all prior baseline dates remain above minimum and every balance from the payment date through the forecast end remains above minimum after subtracting the full request. Return `None` when no valid date exists. |
| Decision Stage 1 statuses | Baseline-only classification returns `affordable_now` when the full request fits today, `affordable_later` when a later baseline date is safe, and `not_affordable` otherwise. `affordable_with_plan` requires a later validated plan/scenario and is deferred. |
| Decision Stage 1 validation | Use Decimal-only arithmetic, reject negative requests and deadlines before the request date, and preserve deterministic behavior. Decision tests plus all existing tests passed 70/70. |
| Payment-option source of truth | Evaluate only options supplied for the request in `request_payment_options.csv`; preserve supplied installment amount, count, frequency, fees, dates, and option ID exactly. |
| Payment-option validation | Reject malformed options, unsupported methods/frequencies, options after the deadline, and installment counts above the profile limit. Full-payment options must match the requested amount exactly. |
| Plan simulation | Apply each scheduled payment cumulatively to every forecast date from that payment onward; reject any plan that breaches the minimum balance, even if its final balance is positive. |
| Spending-change evaluation | Evaluate only generated recurring flexible-event changes validated by forecast permissions and minimum amounts. Never mutate baseline events or the baseline forecast. |
| Decision Stage 2 ranking | Rank feasible candidates by deadline completion, no spending changes, total payable amount, earlier start, fewer payments, and lowest option ID. |
| Decision Stage 2 status | Prefer `affordable_now` when baseline full payment is safe; otherwise use `affordable_with_plan` only for a validated supplied/partial/scenario plan, `affordable_later` for baseline waiting, and `not_affordable` otherwise. |
| Decision Stage 2 result | Expose exact plan text, selected option ID/details, spending changes, baseline result, and selected forecast while preserving Stage 1 fields and semantics. |
| Integration validation | Validate all 250 requests using the existing loader and preserve explicit failures. Missing exact exchange-rate dates and unresolved cash-impacting amounts remain blocked rather than receiving unsafe fallback values. |
| Derived partial payment | `partial_payment` is a derived recommendation when explicitly allowed and safe; it is not required to match a row in `request_payment_options.csv`. Supplied option ownership checks apply only to dataset-backed options. |
| Payment timing regression | Payment simulation applies each payment on its exact date and carries it only forward. Payments sharing a date are summed deterministically. Future expenses and income are evaluated between installments. |
| Dataset validation result | 224/250 requests produced decisions; 24 had exact-rate coverage gaps and 2 had unresolved cash amounts. These are documented data/coverage limitations, not silently downgraded decisions. |
| Output schema | Write exactly `request_id`, `amount_safe_to_pay`, `affordability_status`, `recommended_payment_method`, `payment_plan`, `earliest_date_for_full_payment`, `spending_changes_needed`, and `decision_explanation`, in that order. |
| Output formatting | Quantize money to two decimals with `ROUND_HALF_UP`, format dates as ISO `YYYY-MM-DD`, and preserve chronological payment plans with deterministic separators. |
| Blocked output rows | For unresolved amounts or missing exact FX data, leave decision fields blank and provide a factual safety explanation. Never invent a status, amount, date, or payment plan. |
| Output validation | Validate complete request coverage, unique IDs, amount bounds, allowed enumerations, date/deadline rules, partial-plan completeness, and exact supplied full/installment plan matching. |
| Output generation result | Generated and independently validated 250 rows with 8 columns. The run contained 224 safe decisions and 26 explicit blocked rows; no source dataset or LLM/API usage was added. |
| Affordable-now preference rule | `affordable_now` requires both baseline full-payment safety and `full_payment` in the user's accepted methods. If full payment is not accepted, a valid installment/partial plan is `affordable_with_plan`; without an accepted safe route, the request is not recommended. |
| Explanation consistency | Explanations must describe the selected method. Installment and partial-payment recommendations must not say the full amount is paid immediately. |
| Final audit | Recomputed all 224 safe decisions, simulated all 52 selected installment plans, checked all four partial plans, and validated all 26 blocked rows. No further logic defects were found after the preference-rule fix. |
| Scenario audit | Real forecasts contain flexible recurring expense templates, but no real request required a spending-change scenario. Existing synthetic scenario tests remain the evidence for bounded permitted changes; real output was not altered to create such cases. |
| Image evidence policy | A blank event amount may be filled only from a manually reviewed linked image when amount, currency, event identity, and semantics agree. Conflicting or ambiguous images remain unresolved and continue to block affected forecasts. |
| Image audit result | Nine image-linked events had consistent, clear evidence and are resolved in-memory; seven were intentionally rejected because the document conflicted with the structured event or could not establish identity. No image overrides structured data on conflict. |
| Blocking image cases | Image 10 visibly shows INR 2,298 for a tote-bag order, but event 6033 describes a grocery invoice, so it cannot resolve that event. Image 11 visibly shows hospital amount payable INR 3,650 and matches event 6859, resolving the second cash-amount block. |
| Image implementation boundary | Image evidence is isolated in `code/image_evidence.py`; it does not alter affordability, payment ranking, minimum-balance, FX, or output validation rules. No OCR or LLM/API call was used. |
| Runtime image compliance | Image records are discovered from `images.csv`; linked media paths are resolved dynamically by image ID and opened at runtime. Optional local OCR produces candidate evidence only; missing OCR, ambiguity, missing files, or conflicts leave amounts unresolved. No event/image-specific financial values are embedded in production code. |
| Output input boundary | `output.csv` is an output artifact, not a required input to `load_all()`. |
| Image extractor boundary | `ImageEvidenceExtractor` returns only candidate amount/currency/document/identity evidence. It cannot produce affordability or payment decisions. |
| Image backend policy | Use an explicitly configured local VLM command or locally available Tesseract backend; never download models, require credentials, or call external services automatically. |
| Image validation policy | Require one linked readable image, one positive amount, matching currency, confidence at least 0.70, and compatible identity evidence. Reject malformed, ambiguous, conflicting, or unavailable evidence. |
