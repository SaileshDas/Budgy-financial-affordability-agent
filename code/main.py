"""Application entry point for deterministic affordability output generation."""

from __future__ import annotations

from pathlib import Path

from code.decision import evaluate_stage2_decision
from code.forecast import build_baseline_forecast
from code.loaders import load_all
from code.output import blocked_row, result_row, validate_output_rows, write_output


def generate_output(dataset_dir: str | Path | None = None) -> Path:
    data = load_all(dataset_dir)
    output_path = (Path(dataset_dir) if dataset_dir is not None else Path(__file__).resolve().parent.parent / "dataset") / "output.csv"
    rows = []
    for request in data["requests.csv"]:
        try:
            result = evaluate_stage2_decision(data, request)
            rows.append(result_row(request, result, data))
        except (ValueError, TypeError, ArithmeticError) as error:
            rows.append(blocked_row(str(request["request_id"]), error))
    validate_output_rows(rows, data["requests.csv"], data["request_payment_options.csv"])
    write_output(rows, output_path)
    return output_path


if __name__ == "__main__":
    generate_output()
