# Development Log

## Completed work

- Audited the dataset schemas, relationships, categories, and edge cases.
- Chose a simple modular architecture with deterministic financial processing.
- Implemented the initial CSV loader with validation, date normalization, Decimal monetary parsing, and missing-value preservation.
- Ran the initial loader tests: 5/5 passed.
- Adopted a hybrid pandas approach: pandas handles CSV ingestion and tabular processing; Decimal-based Python logic handles financial arithmetic and safety decisions.
- Implemented `events.py` normalized cash-flow records using `settlement_date` as the cash-impact date. Missing amounts remain unresolved and are never treated as zero.
- Event handling treats settled debits and credits as cash-impacting; pending and scheduled debits as future obligations; pending and scheduled credits conservatively as excluded until settled; and failed, cancelled, unrealized, and `non_cash` events as non-cash-impacting.
- Added event filtering and grouping helpers. Event tests passed: 10/10.
- Implemented exact direct dated exchange-rate lookup in `currency.py`. No inverse, cross-currency, or nearest-date fallback is used.
- Currency conversion uses Decimal-only monetary arithmetic. Same-currency conversion requires no rate, and missing or invalid rates raise errors.
- Converted cash flows preserve the original event and add the signed home-currency amount. Currency tests passed: 8/8.

## Explicit assumptions

- Pending and scheduled credits are excluded conservatively until they are settled.

## Message analysis milestone

- Analyzed all 215 rows in `dataset/messages.csv`. The five source types are employer (126), service provider (31), financial service (23), bank (18), and merchant (17).
- The messages contain regular, template-like notices but vary in wording and language. Approximately 50-61 are clearly Indonesian and the remainder are primarily English.
- Identified the main fact families: payroll and salary updates (about 127), unconfirmed or variable income (about 19), employment or seasonal-contract endings (about 20), refunds (about 14), failed debits (4), open disputes (6), internal account transfers (5), unrealized investment valuations (6), realized investment sales (3), prize or claim proceeds (about 15), and service/payment/rent/invoice changes (about 95; categories overlap).
- There are 39 messages with `related_event_id`; every nonblank related event identifier matched `financial_events.csv`. There are 128 nonblank `request_id` values; 116 matched `requests.csv` and 12 matched the sample-request range rather than the target request file. The remaining messages have no request link.
- Messages confirm existing events when they state that a payment, credit, prize, investment sale, or receipt settled. They amend events when they provide a new salary, payment date, rent amount, deduction, refund state, or failed/disputed status. They override earlier timing or amount information only when the message explicitly says it replaces or revises the prior value.
- Pending, unapproved, expected-but-not-credited, disputed-without-reversal, unrealized-investment, and internal-transfer notices should not create spendable cash. Some messages provide information not represented directly in events, such as salary changes, employment ending, and permission/timing context.
- Recommended message design: a deterministic parser for explicit validated patterns, with an LLM fallback only for genuinely ambiguous interpretation. The LLM must not make safety decisions or invent amounts/dates; ambiguous results should be ignored or surfaced for review.
- Representative patterns include: “Gaji bulanan ... naik menjadi IDR 42750000 ... berlaku mulai 2025-08-15”; “Your next salary is reduced to EUR 1422.85”; “Your employment has ended”; “Your refund has been initiated but has not reached your account yet”; “The previous debit attempt failed ... another debit will be attempted”; “matching debit and credit came from a transfer between your two accounts”; “No units have been sold and no cash proceeds have been generated”; and “proceeds from your investment sale have settled in the cash account.”
- Message analysis did not change business logic. The parser design is an explicit assumption for the next implementation milestone.

## Message parser implementation

- Implemented `messages.py` as a deterministic-first parser using the existing `LoadedData` tables.
- Added structured `MessageFact` and `ParsedMessage` representations, preserving message context and separating parsed, ambiguous, and no-action outcomes.
- Added validated parsing for salary/income amounts and dates, employment endings, refunds, failed debits, disputes, internal transfers, unrealized investments, realized investment sales, payment/rent changes, unapproved income, and confirmed employer reimbursements.
- Added English and Indonesian keyword handling, Decimal monetary extraction, percentage extraction, explicit replacement/override detection, and support for multiple independent facts in one message.
- `related_event_id` is validated against `financial_events.csv` and is the only direct event association. `user_id` and `request_id` are validated contextual fields; text-only event associations are never invented.
- Pending, unapproved, expected, non-withdrawable, disputed-without-reversal, unrealized, and internal-transfer facts are marked non-spendable.
- Added an injectable LLM fallback extension point, but no LLM or API is called. The deterministic parser remains fully functional without it.
- Message parser tests passed: 13/13. Existing loader, event, and currency suites also passed: 23/23.
- A read-only run over all 215 dataset messages produced 184 parsed messages and 31 no-action messages, with no invalid event-reference ambiguities.

### Message parser assumptions

- Employment-ending facts affect future income only from an explicitly stated date; when no date is stated, the fact is retained for later conservative handling rather than inventing a date.
- A message changes or overrides an earlier fact only when explicit amendment language is present; message recency alone is insufficient.
- Pending refunds and other unconfirmed inflows never become spendable cash.
- Ambiguous or unsupported wording produces `no_action` rather than an inferred financial amendment.

## Experimental no-action LLM benchmark

- Ran the existing deterministic parser and isolated exactly 31 messages with disposition `no_action`: `message_02`, `message_16`, `message_17`, `message_18`, `message_28`, `message_34`, `message_35`, `message_48`, `message_64`, `message_67`, `message_71`, `message_75`, `message_79`, `message_84`, `message_88`, `message_94`, `message_98`, `message_99`, `message_110`, `message_118`, `message_134`, `message_136`, `message_142`, `message_144`, `message_158`, `message_161`, `message_177`, `message_199`, `message_208`, `message_209`, and `message_213`.
- This was an experiment only and was separate from the production pipeline. No LLM provider or configured API key was available in the environment, so no messages were sent externally and no LLM output was accepted or applied.
- Benchmark result: 31 deterministic no-action messages, 0 LLM financial facts, 0 LLM ambiguous results, 0 LLM no-action results, 0 validated additional facts, 0 rejected interpretations, and 0 API calls.
- Because the provider was unavailable, the benchmark provides no evidence that an LLM adds measurable value. Recommendation: **A. Keep deterministic-only** for the current implementation. Revisit a narrowly scoped fallback only after a controlled provider-backed experiment.

## Forecast design milestone

- The forecast engine will evaluate each request independently from the user's `financial_profiles.current_available_balance` on that request's `request_date`, using the profile's home currency and minimum balance.
- Forecast cash flows will come from normalized events, use `settlement_date` as the cash date, and convert foreign amounts through the exact direct dated-rate rules in `currency.py`. Validated message facts will be applied as explicit amendments before projection; message text alone will never create an event.
- Status policy remains conservative: settled credits/debits are included, pending and scheduled debits are future obligations, pending/scheduled credits are excluded, and failed, cancelled, unrealized, and non-cash records are excluded. Unresolved amounts must be resolved by the image layer before they can enter a cash forecast; they are never zero.
- Recurrence inference will be deterministic and per user. A recurring template must have repeated compatible records with the same category, direction, currency, flexibility, and stable amount, plus a consistent interval such as weekly/biweekly/monthly. A single event or irregular evidence is not projected. Fixed, reducible, stoppable, and reducible-or-stoppable flexibility will be retained on each recurring template.
- The forecast horizon will cover at least 90 days from the request date and extend through the requested completion deadline when later. The timeline will include all event dates, recurring dates, message amendment effective dates, and request/payment dates.
- Spending changes will not be applied to the baseline. They will be represented as separate validated scenarios containing only permitted recurring flexible events, with stop and reduce actions mutually exclusive per event. `amount_safe_to_pay` and earliest full-payment capacity will use the no-change baseline.
- Internal calculations will use `Decimal` only. Comparisons will use exact Decimal values; values will be quantized to two decimal places with `ROUND_HALF_UP` only at the output/payment-boundary.
- Proposed forecast interfaces are `build_baseline_forecast`, `build_scenario_forecast`, `balance_on`, `minimum_projected_balance`, and `available_surplus`. A forecast result will retain dated balances and applied cash-flow records so decisions can explain the minimum-balance constraint.
- Forecast design is analysis only; `forecast.py` and forecast tests were not implemented in this milestone.

## Forecast Stage 1 implementation

- Implemented baseline daily cash-flow forecasting in `forecast.py`.
- Each forecast starts from `financial_profiles.current_available_balance` on the request date and uses the profile home currency and required minimum balance.
- The forecast covers the request date through at least 90 days later, extending through a later requested completion deadline.
- Cash timing uses event `settlement_date`. Settled debits/credits and pending/scheduled debits are included; pending/scheduled credits, failed, cancelled, unrealized, and non-cash events are excluded.
- Foreign-currency flows are converted using the existing exact direct settlement-date conversion logic. Missing amounts propagate `UnresolvedAmountError` and are never treated as zero.
- Added typed daily forecast records containing opening balance, inflows, outflows, closing balance, minimum required balance, and converted source cash flows.
- Added downstream query helpers for balance on a date, minimum projected balance, and available surplus above the required minimum.
- Stage 1 intentionally does not implement recurrence inference, message amendments, spending-change scenarios, payment plans, or affordability decisions.
- Focused forecast tests passed: 13/13. Complete test suite passed: 49/49.

## Forecast Stage 2 implementation

- Added conservative deterministic recurrence inference for repeated settled records with stable user/category/direction/currency/amount/flexibility and weekly, biweekly, or monthly intervals. One-off, irregular, failed/cancelled, transfer, refund, investment, unresolved, and unconfirmed records are not projected.
- Added validated message amendments using the existing parser. Explicit salary changes apply from their stated effective date; dated employment endings stop future salary recurrence; completed refunds linked to an existing event are added at their supported cash date. Pending refunds, pending or unapproved income, internal transfers, open disputes, and unrealized investments remain non-spendable.
- Added independent spending-change scenarios using `stop:<event_id>` and `reduce_to:<event_id>:<amount>`. Changes require recurring flexible events and matching profile permissions, respect minimum allowed amounts, and cannot combine stop and reduce for one event. The baseline remains unchanged.
- Extended forecast results with recurring templates, applied amendments, applied spending changes, baseline linkage, and daily scenario balances while retaining Decimal-only calculations and the Stage 1 API.
- Added focused Stage 2 coverage for recurrence intervals, irregular/conflicting records, amendments, conservative exclusions, scenarios, permissions, minimum reductions, baseline isolation, and Decimal behavior.
- Complete test suite passed: 60/60.

## Decision Stage 1 implementation

- Implemented deterministic baseline decision helpers in `decision.py` for `amount_safe_to_pay`, `affordability_status`, and `earliest_date_for_full_payment`.
- Safe payment capacity is `min(requested_amount, max(0, minimum baseline closing balance - required minimum balance))` across the full forecast horizon. This protects fixed/essential obligations already represented in the baseline and never assumes optional spending changes.
- Earliest full-payment date is the first date at which the full request can be paid as a single payment while all prior baseline dates remain at or above minimum and all subsequent projected balances remain at least the minimum after the payment. An unavailable date is represented as `None`, matching the output convention of leaving the field empty.
- Stage 1 status classification returns `affordable_now`, `affordable_later`, or `not_affordable` from baseline capacity and timing. `affordable_with_plan` is reserved for later payment-plan or permitted-spending-change stages and is not inferred without such a validated option.
- All request amounts and calculations require `Decimal`; negative requested amounts and invalid deadlines are rejected.
- Added ten focused decision tests. Complete test suite passed: 70/70.

## Decision Stage 2 implementation

- Added validation for only the request's supplied `full_payment` and `installments` options. Schedules use the supplied amount, count, frequency, fee, dates, and option ID without inventing or altering installments.
- Installments accept only the supplied 28-, 30-, or 31-day frequencies, respect `max_installment_months`, and must finish on or before the requested completion date.
- Added payment-plan simulation against every daily baseline/scenario balance. Each cumulative payment is applied on its scheduled date and all later dates must remain at or above the required minimum balance.
- Added permitted spending-change evaluation using Stage 2 forecast scenarios. Candidate stop/reduce changes are generated only for recurring flexible expenses and are validated by the forecast/profile permission rules. The baseline forecast remains unchanged.
- Added deterministic plan ranking: completion by deadline, fewer spending changes, lower total payable amount, earlier start, fewer payments, then lowest option ID.
- Added support for meaningful two-payment `partial_payment` recommendations when explicitly allowed and accepted, while preserving the baseline `amount_safe_to_pay`.
- Extended decision results with recommended method, selected option, exact payment-plan text, spending changes, baseline result, and selected forecast.
- Complete suite after Stage 2 implementation: 77/77 tests passed.

## Decision integration and dataset validation

- Ran the deterministic decision engine against all 250 target requests using the existing loader.
- 224 requests produced decisions successfully. The successful status distribution was: `affordable_now` 92, `affordable_with_plan` 27, `affordable_later` 39, and `not_affordable` 66.
- The recommendation distribution was: `full_payment` 61, `installments` 52, `partial_payment` 4, `wait` 39, and `not_recommended` 68.
- No successful request required spending changes. Fifty-two successful requests used installments.
- Twenty-four requests were blocked by missing exact dated direct exchange rates for recurring foreign-currency cash flows. Two were blocked by unresolved amounts on cash-impacting events. These are explicit data/coverage limitations under the exact-rate and unresolved-amount safety rules, not reasons to impute values.
- The first integration assertion incorrectly treated the synthetic `partial_payment` selection as if it had to be a supplied option. The validation criteria were corrected; partial payment is a derived recommendation and is not a payment-option dataset row.
- Supplied installment amounts and schedules were preserved. No selected plan violated its request ownership, deadline, or minimum-balance constraints.
- Fixed `_simulate_plan` so multiple payments on the same date are summed instead of one silently replacing another. Payment timing regression tests now cover pre-payment expenses, later expenses, and intervening income.
- The complete suite passed: 80/80.

## Output generation stage

- Implemented the deterministic output pipeline in `code/main.py`: load all data, evaluate every request, serialize safe decisions, represent blocked requests without invented financial values, validate rows, and write `dataset/output.csv`.
- Added `code/output.py` with the exact eight-column schema and required column order.
- Monetary output is quantized to two decimals with `ROUND_HALF_UP`; dates use ISO `YYYY-MM-DD`; payment plans are chronological and human-readable.
- Explanations are generated from request, profile, and decision facts without LLM use. They mention the selected method/plan and spending changes only when present.
- Blocked requests use the dataset's blank decision-field convention. Their explanation identifies unresolved or missing financial data; no status, amount, date, or recommendation is fabricated.
- The validator checks request coverage, duplicate IDs, allowed statuses and methods, amount bounds, date/deadline rules, plan syntax, partial-payment completion, and exact matching of full/installment plans to supplied options.
- The complete suite passed: 85/85.
- Real output generation produced 250 rows and 8 columns. There were 224 safe decisions and 26 blocked rows: 24 exact-FX coverage failures and 2 unresolved cash amounts.
- Output statuses: `affordable_now` 92, `affordable_with_plan` 27, `affordable_later` 39, `not_affordable` 66, blocked 26.
- Output methods: `full_payment` 61, `installments` 52, `partial_payment` 4, `wait` 39, `not_recommended` 68, blocked 26. Payment plans were `none` for 133 rows; no output row required spending changes.

## Final real-data quality audit

- Audited the generated output against all 250 requests, profiles, payment options, forecasts, and the output contract. The audit checked every selected plan by recomputing the decision and simulating the selected forecast.
- Found and fixed one genuine logic defect: `affordable_now` had been assigned whenever baseline capacity allowed full payment, even when the user's accepted payment methods excluded `full_payment`. The status now requires accepted `full_payment`; otherwise a valid selected plan is `affordable_with_plan`, or the request is classified according to its accepted safe options.
- Updated explanations so installment and partial-payment recommendations do not claim that the full amount is being paid immediately.
- Added regression coverage requiring `affordable_now` to recommend `full_payment`.
- After the fix, the final distribution is: `affordable_now` 61, `affordable_with_plan` 56, `affordable_later` 38, `not_affordable` 69, and blocked 26. Methods are `full_payment` 61, `installments` 52, `partial_payment` 4, `wait` 38, `not_recommended` 69, and blocked 26.
- All 52 selected installment plans matched supplied option amounts, counts, frequencies, first dates, and totals, completed by deadline, and passed minimum-balance simulation. All four partial-payment plans were explicitly allowed and completed the requested amount in exactly two payments.
- The dataset contains flexible recurring templates (209 stoppable, 36 reducible-or-stoppable, and 26 reducible observations across request forecasts), but no successful final recommendation required a spending change. Existing synthetic scenario tests confirm permitted stop/reduction behavior; no real-data result was forced.
- All 26 blocked rows have blank decision fields and factual explanations: 24 exact-FX coverage failures and 2 unresolved cash amounts. They are not counted as `not_affordable`.
- The final 20-explanation sample and broader method-specific explanation checks found no unsupported amount, currency, plan, or spending-change claims.
- Final audit suite passed: 86/86. The deterministic pipeline is ready for final submission. LLM integration is not justified for safety decisions; if added later, it should be limited to optional explanation polishing after deterministic validation.

## Real-data image audit

- Inspected all 16 image files and verified every `images.csv` file exists. All referenced event IDs exist; image request IDs `request_03` through `request_20` for images 01-05 are absent from the 250-row target request table and were not silently remapped.
- Clearly consistent image evidence resolved amounts for events 253 (IDR 4,365,000), 1700 (INR 41,272), 3051 (INR 1,995), 4535 (INR 15,339), 5170 (INR 723), 6859 (INR 3,650), 7307 (USD 33.50), 9806 (INR 9,968), and 10521 (INR 393.22).
- Images 02, 03, 05, 07, 10, 13, and 14 contain visible amounts but conflict with the linked structured event's merchant/category/description or have ambiguous identity. They remain unresolved. In particular, image 10 is a tote-bag order while event 6033 is a large grocery invoice, so request 64 remains blocked.
- Image 11 is a hospital provisional bill with a clear INR 3,650 payable balance and matches event 6859. A small evidence-resolution layer uses this reviewed value without changing decision, payment-plan, or minimum-balance rules.
- No OCR or external/model API was used. Image evidence is accepted only when currency and event semantics are consistent; conflicting images are not allowed to override structured facts.
- Added focused image-evidence tests. The full suite passed 89/89 and `python -m code.main` regenerated the output. Independent validation found 250 rows, 8 columns, unique complete request coverage, status counts of 62 affordable_now, 56 affordable_with_plan, 38 affordable_later, 69 not_affordable, and 25 blocked. Payment methods were 62 full_payment, 52 installments, 4 partial_payment, 38 wait, 69 not_recommended, and 25 blocked.
- The remaining blocked requests are 24 missing exact-FX cases and one unresolved/conflicting cash amount. No dataset files or usage-report claims were changed, and multimodal LLM integration is not justified by this audit.

## Submission compliance remediation

- Removed `output.csv` from the production input-file list. The generator now starts from the input CSV files and writes `output.csv` without requiring a previous output.
- Removed dataset-specific image amount mappings from production code.
- Added runtime local-media resolution from `images.csv` metadata and dynamic image opening with Pillow. Optional local OCR is used only when available; unavailable or ambiguous extraction remains unresolved.
- Image evidence is accepted only for a uniquely linked event/user, an existing readable image, one positive extracted amount, and one matching currency. Structured nonblank amounts are never overridden.
- Financial affordability and payment safety remain deterministic; no model or external API was added.

## Optional image extractor implementation

- Added a generic `ImageEvidenceExtractor` protocol with structured candidate evidence.
- Added backend detection for an explicitly configured local VLM command and for `pytesseract` plus a discoverable Tesseract executable. No model or binary is downloaded automatically.
- Pillow always opens and verifies the dynamically linked local image before extraction.
- Candidate evidence is accepted only when there is exactly one linked image, one positive Decimal amount, matching currency, sufficient confidence, and compatible identity evidence. Malformed, ambiguous, conflicting, or unavailable extraction remains unresolved.
- No affordability, payment, or amount-safe decision can be produced by an extractor.
- In the final local environment, Pillow was available but no OCR package/executable or configured VLM was available. Therefore zero image-derived amounts were resolved and image-dependent amounts remained safely unresolved.
- `output.csv` remains write-only for production loading. The full test suite passed 96/96, and clean-state generation produced 250 rows with 224 classified and 26 blocked.
