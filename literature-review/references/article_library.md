# Reading, converting, and filing papers

This is the token-efficient reading pipeline for a single researcher's thesis:
convert each PDF to Markdown once, read the text, then file the paper into a
flat, auditable library with a one-line reason or a short summary. It sits
before Phase 4 (data extraction) and complements PRISMA screening: every filing
decision is recorded, so `Dropped` reasons feed the excluded-studies appendix.

## Why convert PDFs to Markdown

A PDF costs far more tokens than its text and some agents cannot open one at
all. Extract the text to `.md` once with `scripts/pdf_to_markdown.py`, then read
the Markdown. Conversion is local: nothing is uploaded.

```bash
# One paper, next to the original
python scripts/pdf_to_markdown.py inbox/delivery_2024.pdf

# A whole inbox into a reading folder
python scripts/pdf_to_markdown.py inbox --recursive --output-dir reading/

# Machine-readable result, then check the extraction
python scripts/pdf_to_markdown.py inbox --json
```

Backends are tried in order: `pymupdf4llm` (layout-aware Markdown), then
`markitdown`, `liteparse`, and plain `pymupdf`. Install at least
`pymupdf4llm`; the others are other skills' libraries and are optional. Pass
`--backend` to pin one.

Extraction is only as good as the PDF. If the output is shorter than
`--min-chars` (default 200) the script warns: the PDF is probably scanned and
needs OCR (see the `markitdown` and `liteparse` skills) or a layout parser.
Never treat a near-empty extraction as a read paper.

## Library layout

`scripts/organize_library.py` owns this structure and a `catalog.json` source of
truth. `SUMMARY.md` is regenerated from the catalogue on every change; never
hand-edit it.

```text
library/
├── Relevant/
│   ├── base-editing/
│   │   ├── paper.md
│   │   └── paper.pdf        # sibling PDF, copied when present
│   └── delivery/
│       └── another.md
├── Dropped/
│   └── off-topic.md
├── ?/                       # Questions: cannot decide yet
│   └── unclear.md
├── catalog.json
└── SUMMARY.md
```

- **Relevant** - fits the research. One subdirectory per dissertation subtopic,
  and a `--summary` of what the paper contributes.
- **Dropped** - does not fit the research at all. A one-line `--reason`.
- **? (Questions)** - the agent cannot yet decide; record the uncertainty in a
  one-line `--reason` so it can be revisited.

Reasons for `Dropped` and `?` are capped at `--max-reason-words` (default 15):
one short sentence. Summaries for `Relevant` papers have no hard cap; two to
four sentences is the useful range.

## Filing one paper

Convert first, read the `.md`, then file it.

```bash
# Relevant: per-subtopic directory plus a summary
python scripts/organize_library.py file reading/delivery_2024.md \
  --library library --category relevant --topic "LNP delivery" \
  --summary "Shows lipid nanoparticles improve in-vivo editing efficiency." \
  --year 2024 --doi 10.1000/example

# Dropped: the paper cannot support the research
python scripts/organize_library.py file reading/weather_model.md \
  --library library --category dropped \
  --reason "Atmospheric model, unrelated to gene editing."

# Uncertain: a deliberate holding bucket, not a silent drop
python scripts/organize_library.py file reading/maybe.md \
  --library library --category questions \
  --reason "Unclear whether the sample is human or murine."
```

- Passing the `.md` copies it and its sibling `.pdf`; pass `--move` to relocate.
- A duplicate DOI or title is refused rather than filed twice.
- A filename collision is suffixed (`paper-2.md`); `--overwrite` replaces it.
- `--title`, `--year`, `--doi`, and `--authors` enrich the catalogue; the title
  otherwise comes from the first Markdown heading.

## Reviewing and reporting

```bash
python scripts/organize_library.py list --library library --category relevant
python scripts/organize_library.py report --library library   # rewrites SUMMARY.md
```

`SUMMARY.md` groups `Relevant` papers by subtopic and lists `Dropped` and `?`
papers with their reasons, so the whole corpus can be reviewed without opening a
single paper. Use the `Dropped` reasons for the excluded-studies table
(Appendix C of `assets/review_template.md`) and revisit `?` when the subtopic
structure becomes clearer; re-file a paper by editing the catalogue and running
`report`, or by re-filing with a cleared entry.

## Scope and caveats

- This pipeline stores and summarizes papers; it does not decide relevance for
  you, assess risk of bias, or replace full-text screening.
- A summary is the agent's synthesis, not the authors' abstract. Verify any
  claim against the Markdown before it enters the review.
- Keep the original PDFs. Markdown extraction can drop equations, tables, and
  figures; consult the PDF when a number or a figure matters.
