#!/usr/bin/env python3
"""File read papers into a dissertation library and keep a summary catalogue.

After a paper is read as Markdown, the agent classifies it into exactly one
bucket and records why:

* `Relevant` - fits the research; filed under a per-subtopic subdirectory, with
  a summary of what it contributes.
* `Dropped` - does not fit the research at all; recorded with a one-line reason.
* `?` (Questions) - the agent cannot yet decide; recorded with a one-line reason
  for the uncertainty.

The directory layout and a `catalog.json` are the source of truth; `SUMMARY.md`
is regenerated from the catalogue on every change. Article files are copied by
default so the original inbox is preserved; pass `--move` to relocate them.

Reasons for `Dropped` and `?` are deliberately short (default maximum 15 words)
so the catalogue stays scannable. Summaries for `Relevant` papers are not
length-limited, but two to four sentences is the useful range.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

RELEVANT = "Relevant"
DROPPED = "Dropped"
DEFAULT_QUESTIONS_DIR = "?"
CATEGORY_DIRS = {"relevant": RELEVANT, "dropped": DROPPED, "questions": DEFAULT_QUESTIONS_DIR}
CATEGORIES = ("relevant", "dropped", "questions")
DEFAULT_MAX_REASON_WORDS = 15
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")


class LibraryError(ValueError):
    """A request that cannot be filed as given (bad category, missing input)."""


def slugify(text: str) -> str:
    """ASCII, hyphenated, filesystem-safe slug; never empty."""
    decomposed = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", decomposed).strip("-").lower()
    return slug or "untitled"


def normalize_title(title: str) -> str:
    """Case- and punctuation-insensitive key for duplicate detection."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def word_count(text: str) -> int:
    return len(text.split())


def category_directory(category: str, questions_dir: str) -> str:
    if category == "questions":
        return questions_dir
    return CATEGORY_DIRS[category]


def _catalog_path(root: Path) -> Path:
    return root / "catalog.json"


def init_library(root: Path, questions_dir: str = DEFAULT_QUESTIONS_DIR) -> Path:
    """Create the bucket directories and an empty catalogue; idempotent."""
    for name in (RELEVANT, DROPPED, questions_dir):
        (root / name).mkdir(parents=True, exist_ok=True)
    catalog_path = _catalog_path(root)
    if not catalog_path.exists():
        catalog_path.write_text(json.dumps({"version": 1, "articles": []}, indent=2) + "\n",
                                encoding="utf-8")
    return root


def load_catalog(root: Path) -> Dict:
    catalog_path = _catalog_path(root)
    if not catalog_path.is_file():
        return {"version": 1, "articles": []}
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LibraryError(f"cannot read {catalog_path}: {exc}") from exc
    if not isinstance(catalog, dict) or not isinstance(catalog.get("articles"), list):
        raise LibraryError(f"{catalog_path} is not a library catalogue")
    return catalog


def save_catalog(root: Path, catalog: Dict) -> None:
    _catalog_path(root).write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")


def title_from_markdown(path: Path, fallback: str) -> str:
    """Prefer the top-level (`#`) heading, else any heading, else the fallback stem."""
    first_heading = ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = HEADING.match(line)
            if not match:
                continue
            if line.startswith("# "):
                return match.group(1).strip()
            first_heading = first_heading or match.group(1).strip()
    except OSError:
        pass
    return first_heading or fallback.replace("_", " ").strip() or "Untitled"


def resolve_markdown(source: Path) -> Path:
    """Accept a `.md`, or a `.pdf` whose sibling `.md` already exists."""
    if source.suffix.lower() == ".md":
        return source
    if source.suffix.lower() == ".pdf":
        markdown = source.with_suffix(".md")
        if markdown.is_file():
            return markdown
        raise LibraryError(f"{source} has no Markdown sibling; run pdf_to_markdown.py first")
    raise LibraryError(f"unsupported article file: {source} (expected .md or .pdf)")


def unique_path(path: Path, overwrite: bool) -> Path:
    """First free path, suffixing `-2`, `-3`, ...; an empty file is still taken."""
    if overwrite or not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _transfer(source: Path, destination: Path, move: bool) -> None:
    if move:
        shutil.move(str(source), str(destination))
    else:
        shutil.copy2(str(source), str(destination))


def file_article(root: Path, source: Path, category: str,
                 questions_dir: str = DEFAULT_QUESTIONS_DIR,
                 topic: Optional[str] = None, title: Optional[str] = None,
                 summary: Optional[str] = None, reason: Optional[str] = None,
                 doi: Optional[str] = None, year: Optional[str] = None,
                 authors: Optional[str] = None, move: bool = False,
                 overwrite: bool = False,
                 max_reason_words: int = DEFAULT_MAX_REASON_WORDS) -> Optional[Dict]:
    """Copy one read paper into its bucket and record it. `None` if duplicate."""
    if category not in CATEGORIES:
        raise LibraryError(f"category must be one of {', '.join(CATEGORIES)}")
    source = Path(source)
    if not source.is_file():
        raise LibraryError(f"source not found: {source}")
    markdown = resolve_markdown(source)

    summary = (summary or "").strip()
    reason = (reason or "").strip()
    if category == "relevant":
        if not topic or not topic.strip():
            raise LibraryError("Relevant papers need --topic <subtopic>")
        if not summary:
            raise LibraryError("Relevant papers need --summary of what they contribute")
    else:
        if not reason:
            raise LibraryError(f"{category} papers need --reason")
    if reason and max_reason_words and word_count(reason) > max_reason_words:
        raise LibraryError(
            f"reason must be <= {max_reason_words} words (got {word_count(reason)}); "
            "keep it to one short sentence")

    init_library(root, questions_dir)
    catalog = load_catalog(root)

    title = (title or "").strip() or title_from_markdown(markdown, markdown.stem)
    key = normalize_title(title)
    for existing in catalog["articles"]:
        if doi and existing.get("doi") and doi.strip().lower() == existing["doi"].strip().lower():
            return None
        if key and key == normalize_title(existing.get("title", "")):
            return None

    if category == "relevant":
        destination_dir = root / RELEVANT / slugify(topic)
    else:
        destination_dir = root / category_directory(category, questions_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    markdown_target = unique_path(destination_dir / markdown.name, overwrite)
    pdf_source = markdown.with_suffix(".pdf")
    pdf_target = None
    _transfer(markdown, markdown_target, move)
    if pdf_source.is_file():
        pdf_target = unique_path(destination_dir / pdf_source.name, overwrite)
        _transfer(pdf_source, pdf_target, move)

    record = {
        "title": title,
        "authors": (authors or "").strip(),
        "year": str(year).strip() if year else "",
        "doi": (doi or "").strip(),
        "category": category,
        "topic": (topic or "").strip() if category == "relevant" else "",
        "summary": summary if category == "relevant" else "",
        "reason": reason if category != "relevant" else "",
        "markdown": str(markdown_target.relative_to(root)),
        "pdf": str(pdf_target.relative_to(root)) if pdf_target else "",
        "added": datetime.now().isoformat(timespec="seconds"),
    }
    catalog["articles"].append(record)
    save_catalog(root, catalog)
    write_summary(root, catalog, questions_dir)
    return record


def render_summary(catalog: Dict, questions_dir: str = DEFAULT_QUESTIONS_DIR) -> str:
    """Render the human-readable catalogue as Markdown."""
    articles = catalog.get("articles", [])
    generated = datetime.now().isoformat(timespec="seconds")
    lines = [
        "# Literature Library",
        "",
        "Auto-generated by `organize_library.py`; edit articles and re-run `report`, "
        "do not hand-edit this file.",
        "",
        f"Generated: {generated}",
        f"Articles: {len(articles)}",
        "",
    ]

    relevant = [a for a in articles if a.get("category") == "relevant"]
    topics: Dict[str, List[Dict]] = {}
    for article in relevant:
        topics.setdefault(article.get("topic") or "Unfiled", []).append(article)
    lines.append(f"## {RELEVANT} ({len(relevant)})")
    lines.append("")
    if not relevant:
        lines.append("- _none yet_")
        lines.append("")
    for topic in sorted(topics):
        group = topics[topic]
        lines.append(f"### {topic} ({len(group)})")
        lines.append("")
        for article in group:
            lines.append(_entry(article, "summary"))
        lines.append("")

    for category, heading in (("dropped", DROPPED), ("questions", f"{questions_dir} (Questions)")):
        group = [a for a in articles if a.get("category") == category]
        lines.append(f"## {heading} ({len(group)})")
        lines.append("")
        if not group:
            lines.append("- _none yet_")
            lines.append("")
            continue
        for article in group:
            lines.append(_entry(article, "reason"))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _entry(article: Dict, note_field: str) -> str:
    title = article.get("title", "Untitled")
    year = article.get("year", "")
    suffix = f" ({year})" if year else ""
    note = article.get(note_field, "")
    text = f"- **{title}**{suffix}"
    if note:
        text += f" - {note}"
    location = article.get("markdown", "")
    if location:
        text += f"\n  - `{location}`"
    return text


def write_summary(root: Path, catalog: Dict, questions_dir: str) -> None:
    (root / "SUMMARY.md").write_text(render_summary(catalog, questions_dir), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--library", type=Path, required=True,
                        help="Library root holding Relevant/, Dropped/, and the catalogue")
    common.add_argument("--questions-dir", default=DEFAULT_QUESTIONS_DIR,
                        help="Directory name for the uncertain bucket (default: ?)")

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", parents=[common],
                                        help="Create the bucket directories and catalogue")
    init_parser.set_defaults(handler=_handle_init)

    file_parser = subparsers.add_parser("file", parents=[common],
                                        help="File one read paper into a bucket")
    file_parser.add_argument("source", type=Path, help="Article .md (a sibling .pdf is filed too)")
    file_parser.add_argument("--category", required=True, choices=CATEGORIES)
    file_parser.add_argument("--topic", help="Subtopic subdirectory (required for relevant)")
    file_parser.add_argument("--summary", help="What the paper contributes (required for relevant)")
    file_parser.add_argument("--reason", help="One short sentence (required unless relevant)")
    file_parser.add_argument("--title", help="Override the first Markdown heading")
    file_parser.add_argument("--doi")
    file_parser.add_argument("--year")
    file_parser.add_argument("--authors")
    file_parser.add_argument("--move", action="store_true", help="Move instead of copy")
    file_parser.add_argument("--overwrite", action="store_true",
                             help="Replace an existing filed name instead of suffixing -2")
    file_parser.add_argument("--max-reason-words", type=int, default=DEFAULT_MAX_REASON_WORDS)
    file_parser.set_defaults(handler=_handle_file)

    report_parser = subparsers.add_parser("report", parents=[common],
                                          help="Regenerate SUMMARY.md from the catalogue")
    report_parser.set_defaults(handler=_handle_report)

    list_parser = subparsers.add_parser("list", parents=[common],
                                        help="Print the catalogue as JSON")
    list_parser.add_argument("--category", choices=CATEGORIES)
    list_parser.set_defaults(handler=_handle_list)
    return parser


def _handle_init(args: argparse.Namespace) -> int:
    init_library(args.library, args.questions_dir)
    print(f"[OK] Initialized library: {args.library}")
    print(f"[OK] Buckets: {RELEVANT}/, {DROPPED}/, {args.questions_dir}/")
    return 0


def _handle_file(args: argparse.Namespace) -> int:
    record = file_article(
        args.library, args.source, args.category, questions_dir=args.questions_dir,
        topic=args.topic, title=args.title, summary=args.summary, reason=args.reason,
        doi=args.doi, year=args.year, authors=args.authors, move=args.move,
        overwrite=args.overwrite, max_reason_words=args.max_reason_words,
    )
    if record is None:
        print("[SKIP] Already catalogued (same DOI or title); not filed again")
        return 0
    print(f"[OK] Filed '{record['title']}' in {record['markdown']}")
    return 0


def _handle_report(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.library)
    write_summary(args.library, catalog, args.questions_dir)
    print(f"[OK] Wrote {args.library / 'SUMMARY.md'}")
    return 0


def _handle_list(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.library)
    articles = catalog.get("articles", [])
    if args.category:
        articles = [a for a in articles if a.get("category") == args.category]
    print(json.dumps(articles, indent=2, ensure_ascii=False))
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        raise SystemExit(args.handler(args))
    except LibraryError as exc:
        parser.exit(1, f"[FAIL] {exc}\n")
    except OSError as exc:
        parser.exit(1, f"[FAIL] {exc}\n")


if __name__ == "__main__":
    main()
