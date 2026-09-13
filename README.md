# Budgy — Financial Affordability Agent

Deterministic agent that reads the supplied dataset (`requests.csv`, `financial_profiles.csv`, `financial_events.csv`, `exchange_rates.csv`, `request_payment_options.csv`, `messages.csv`, `images.csv`) and produces `dataset/output.csv` with exactly 8 required columns.

## Requirements

- Python 3.14+
- `pandas`
- `Pillow`

Install:

```bash
pip install -r requirements.txt
```

No external API, model download, or credentials are required.

## Run

```bash
python -m code.main
```

Reads `dataset/` inputs and writes `dataset/output.csv`.

## Tests

```bash
python -m unittest discover -s code/tests -q
```

## Key rules

- Exact dated direct FX only; no nearest/inverse/cross/interpolated rates.
- Blank cash-impacting amounts are unresolved, never treated as zero.
- Image evidence is optional; only validated candidates with matching currency and identity are accepted.
- No LLM/API/model is used in production execution.

## Output schema

`request_id`, `amount_safe_to_pay`, `affordability_status`, `recommended_payment_method`, `payment_plan`, `earliest_date_for_full_payment`, `spending_changes_needed`, `decision_explanation`

## Repository

https://github.com/SaileshDas/Budgy-financial-affordability-agent