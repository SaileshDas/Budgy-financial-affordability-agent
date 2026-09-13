# Usage Report

## Status

The final full-dataset run was performed locally with the deterministic
pipeline. No model, VLM, or external API was used.

## Current development state

- Target requests: 250
- Model calls: 0
- Input tokens: 0
- Output tokens: 0
- Estimated cost: 0
- Total tokens: 0
- Average tokens per request: 0
- Estimated cost per request: 0

This file will be updated after the final run with model providers, model
names, call counts, token usage, averages, and estimated costs. No credentials
or API keys belong in this report.

## Experimental no-action LLM benchmark

This experiment is separate from the production pipeline. It did not modify
the deterministic parser, financial engine, affordability decisions, or
`output.csv`.

- Benchmark subset: exactly 31 messages currently classified as deterministic `no_action`
- Provider: none configured in the environment
- Model: not applicable
- API calls: 0
- Input tokens: 0
- Output tokens: 0
- Estimated cost: 0
- Validated additional facts: 0
- Rejected interpretations: 0

The user-supplied credential was not used or recorded. No message content was
sent to an external provider. The experiment therefore does not establish
whether an LLM would improve interpretation quality.
