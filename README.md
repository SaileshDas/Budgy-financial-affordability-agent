# Budgy financial-affordability agent

## Run

Install the dependencies listed in `requirements.txt`, place the supplied
`dataset/` directory beside `code/`, and run:

```text
python -m code.main
```

The command reads the input CSV files and linked local media, then writes
`dataset/output.csv` with the required eight columns. It does not require a
pre-existing output file.

Run the evaluation workflow with:

```text
python -m unittest discover -s code/tests -q
```

Financial safety decisions are deterministic. Optional local OCR or an
explicitly configured local VLM command may provide candidate image evidence;
all candidates are validated before entering the financial engine. No model,
API key, network access, or automatic model download is required.