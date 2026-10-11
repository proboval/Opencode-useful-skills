# Example Workflow

Illustrative end-to-end recipe, not an executed review. Run from the skill directory; populate the protocol, normalized search records, bibliography, and CSL file before the dependent commands. Dates below are a deliberately historical 2015-2024 interval.

## Example Workflow

Complete workflow for a biomedical literature review:

```bash
# 1. Create review document from template
mkdir -p sources styles
cp assets/review_template.md crispr_sickle_cell_review.md

# 2. Optional supplementary discovery (authenticated Parallel account)
parallel-cli search "CRISPR Cas9 sickle cell disease gene therapy efficacy" \
  -q "CRISPR" -q "sickle cell" -q "gene therapy" \
  --json --max-results 10 --excerpt-max-chars-total 27000 \
  --include-domains "scholar.google.com,arxiv.org,pubmed.ncbi.nlm.nih.gov,semanticscholar.org,biorxiv.org,nature.com,science.org,cell.com,pnas.org,nih.gov" \
  -o sources/litreview_crispr_scd-academic.json

parallel-cli search "CRISPR sickle cell disease clinical trials treatment" \
  -q "CRISPR" -q "sickle cell" \
  --json --max-results 10 --excerpt-max-chars-total 27000 \
  -o sources/litreview_crispr_scd-general.json

# 3. Search specialized databases using appropriate skills
# - Use PubMed E-utilities and bioRxiv/medRxiv search or date-metadata APIs
# - Use direct API access for arXiv, Semantic Scholar
# - Save all pages and counts; document any API caps or failed requests
# - Normalize into a JSON array; keep raw exports and provenance

# 4. Process your normalized combined_results.json; raw API envelopes will fail
python scripts/search_databases.py combined_results.json \
  --deduplicate \
  --year-start 2015 \
  --year-end 2024 \
  --format markdown \
  --output search_results.md \
  --summary

# 5. Screen results, then file every full text you read (so no text is lost)
# - Request extracts with --full-content; obtain original reports and record failures
# - Use independent final full-text decisions and retain exclusion reasons
# - Track records, reports, studies, and overlapping participants separately

# 5a. Convert each obtained report to Markdown once (local text extraction)
python scripts/pdf_to_markdown.py inbox --recursive --output-dir reading/

# 5b. File EVERY read paper into exactly one library bucket; the .md (and .pdf)
#     is copied, so excluded and undecided papers keep their text too.
#     relevant: needs --topic + --summary, filed under a subtopic directory
python scripts/organize_library.py file reading/<report>.md --library library \
  --category relevant --topic "<subtopic>" --summary "<what it contributes>"
#     dropped: needs --reason (papers excluded at full text)
python scripts/organize_library.py file reading/<report>.md --library library \
  --category dropped --reason "<one short sentence>"
#     questions: needs --reason (papers you cannot decide on yet)
python scripts/organize_library.py file reading/<report>.md --library library \
  --category questions --reason "<one short sentence>"
python scripts/organize_library.py report --library library

# 5c. Extract key data into the review document and organize by themes

# 6. Write the review following template structure
# - Introduction with clear objectives
# - Detailed methodology section
# - Results organized thematically
# - Critical discussion
# - Clear conclusions

# 7. Check DOI registration/metadata, then manually verify identity and claim support
python scripts/verify_citations.py crispr_sickle_cell_review.md

# Review the citation report
cat crispr_sickle_cell_review_citation_report.json

# Investigate missing/inconclusive results; transient failures do not invalidate citations
python scripts/verify_citations.py crispr_sickle_cell_review.md

# 8. Render after supplying bibliography, local CSL, and [@key] citations
# For manually typed references, omit those options; text will not be restyled
python scripts/generate_pdf.py crispr_sickle_cell_review.md \
  --bibliography crispr_sickle_cell_review.bib --csl styles/nature.csl \
  --output crispr_sickle_cell_review.pdf

# 9. Review final PDF and markdown outputs
```
