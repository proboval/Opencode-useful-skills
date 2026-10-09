#!/usr/bin/env python3
"""Convert research PDFs to Markdown so an agent reads text, not page images.

Reading a PDF costs many more tokens than reading its extracted text, and some
agents cannot open PDFs at all. This wrapper runs whichever Markdown extractor
is installed, in priority order, and writes one `.md` per PDF next to it (or to
`--output`/`--output-dir`). It downloads nothing and sends no content anywhere.

Extraction is only as good as the PDF. A scanned/image-only PDF yields little or
no text: the script warns when output is shorter than `--min-chars`, and that
document needs OCR (see the `markitdown` and `liteparse` skills) or a layout
parser rather than a repeat run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


class ConversionError(RuntimeError):
    """No backend could turn the file into text."""


def _pymupdf4llm(path: Path) -> str:
    """Layout-aware Markdown built on PyMuPDF; best default for papers."""
    import pymupdf4llm

    return pymupdf4llm.to_markdown(str(path))


def _markitdown(path: Path) -> str:
    """Microsoft MarkItDown's PDF converter (pdfminer-based)."""
    from markitdown import MarkItDown

    result = MarkItDown().convert(str(path))
    return getattr(result, "markdown", None) or result.text_content


def _liteparse(path: Path) -> str:
    """LiteParse's heuristic Markdown extraction (Rust core)."""
    from liteparse import LiteParse

    return LiteParse(quiet=True).parse(str(path)).text


def _pymupdf(path: Path) -> str:
    """Plain PyMuPDF text, kept as a no-Markdown last resort."""
    import pymupdf

    document = pymupdf.open(str(path))
    try:
        return "\n\n".join(page.get_text("text") for page in document)
    finally:
        document.close()


BACKENDS: Dict[str, Callable[[Path], str]] = {
    "pymupdf4llm": _pymupdf4llm,
    "markitdown": _markitdown,
    "liteparse": _liteparse,
    "pymupdf": _pymupdf,
}

#: `auto` tries these in order; layout-aware Markdown first, plain text last.
AUTO_ORDER: Tuple[str, ...] = ("pymupdf4llm", "markitdown", "liteparse", "pymupdf")


def convert_pdf(path: Path, backend: str = "auto") -> Tuple[str, str]:
    """Return `(markdown, backend_used)`; raise `ConversionError` if none work.

    A backend that is not installed, errors, or returns only whitespace is
    skipped so the next one can run. An explicit `backend` never falls back:
    the failure is reported instead of silently changing extractor.
    """
    if backend == "auto":
        order = AUTO_ORDER
    elif backend in BACKENDS:
        order = (backend,)
    else:
        raise ValueError(f"Unknown backend: {backend}")

    problems: List[str] = []
    for name in order:
        try:
            markdown = BACKENDS[name](path)
        except ImportError as exc:
            problems.append(f"{name}: not installed ({exc})")
            continue
        except Exception as exc:  # noqa: BLE001 - report any extractor failure
            problems.append(f"{name}: {exc}")
            continue
        if isinstance(markdown, str) and markdown.strip():
            return markdown, name
        problems.append(f"{name}: produced no text")
    raise ConversionError("; ".join(problems) or "no backend produced text")


def discover_pdfs(inputs: List[Path], recursive: bool) -> List[Path]:
    """Expand files and directories into a de-duplicated, sorted PDF list."""
    found: List[Path] = []
    for item in inputs:
        if item.is_dir():
            pattern = "**/*.pdf" if recursive else "*.pdf"
            found.extend(sorted(item.glob(pattern)))
        elif item.is_file():
            found.append(item)
        else:
            raise FileNotFoundError(item)
    unique = {path.resolve(): path for path in found}
    return [unique[key] for key in sorted(unique)]


def destination_for(source: Path, output: Optional[Path], output_dir: Optional[Path]) -> Path:
    """Pick the `.md` target for one PDF: explicit path, directory, or sibling."""
    if output is not None:
        return output
    if output_dir is not None:
        return output_dir / (source.stem + ".md")
    return source.with_suffix(".md")


def convert_one(source: Path, destination: Path, backend: str, overwrite: bool) -> Dict:
    """Convert a single PDF, returning a machine-readable result record."""
    record = {"source": str(source), "output": str(destination), "backend": "", "status": ""}
    if destination.exists() and not overwrite:
        record["status"] = "skipped"
        return record
    try:
        markdown, used = convert_pdf(source, backend)
    except (ConversionError, ValueError) as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        return record
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(markdown.rstrip("\n") + "\n", encoding="utf-8")
    except OSError as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        return record
    record["backend"] = used
    record["characters"] = len(markdown)
    record["status"] = "converted"
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="PDF files and/or directories")
    parser.add_argument("-o", "--output", type=Path,
                        help="Markdown path; valid only for a single input file")
    parser.add_argument("--output-dir", type=Path,
                        help="Write every .md into this directory")
    parser.add_argument("--backend", choices=("auto", *BACKENDS), default="auto")
    parser.add_argument("--recursive", action="store_true",
                        help="Search directories recursively for .pdf files")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace an existing .md (default: skip it)")
    parser.add_argument("--min-chars", type=int, default=200, metavar="N",
                        help="Warn below this many extracted characters (scanned PDF); 0 disables")
    parser.add_argument("--json", action="store_true", help="Emit a JSON result array on stdout")
    args = parser.parse_args()
    if args.output and args.output_dir:
        parser.error("--output and --output-dir are mutually exclusive")

    try:
        sources = discover_pdfs(args.inputs, args.recursive)
    except FileNotFoundError as exc:
        parser.exit(1, f"[FAIL] not found: {exc}\n")
    if not sources:
        parser.exit(1, "[FAIL] no PDF files matched\n")
    if args.output and len(sources) != 1:
        parser.error("--output needs exactly one input PDF")

    results = []
    for source in sources:
        destination = destination_for(source, args.output, args.output_dir)
        record = convert_one(source, destination, args.backend, args.overwrite)
        results.append(record)
        if record["status"] == "converted":
            print(f"[OK] {source} -> {destination} "
                  f"({record['characters']} chars, {record['backend']})", file=sys.stderr)
            if args.min_chars and record["characters"] < args.min_chars:
                print(f"[WARN] {destination} is shorter than {args.min_chars} characters; "
                      "the PDF may be scanned and need OCR (markitdown / liteparse skills)",
                      file=sys.stderr)
        elif record["status"] == "skipped":
            print(f"[SKIP] {destination} exists; pass --overwrite to replace it", file=sys.stderr)
        else:
            print(f"[FAIL] {source}: {record.get('error', 'conversion failed')}", file=sys.stderr)

    if args.json:
        print(json.dumps(results, indent=2))
    converted = sum(record["status"] == "converted" for record in results)
    failed = sum(record["status"] == "failed" for record in results)
    print(f"[OK] Converted {converted}/{len(results)}; failed {failed}", file=sys.stderr)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
