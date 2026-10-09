#!/usr/bin/env python3
"""Retrieve open-access article PDFs locally into per-topic directories.

Turns a normalised search-results JSON array (the same shape produced for
`search_databases.py`; see references/database_strategies.md) into article files
on disk, so later phases read local copies instead of re-fetching the web. Each
record is saved as:

    <out>/[<topic>/]<index>-<slug>.pdf

and every attempt is written to `<out>/manifest.json` with a status of
`downloaded`, `exists`, `disabled`, or `not_retrieved` (with a reason). Records
without an open-access PDF are kept in the manifest rather than silently
dropped, so full-text screening and PRISMA "reports not retrieved" stay
auditable.

PDF sources, tried in order:

1. an explicit `pdf_url` (or `open_access_pdf`) field on the record;
2. arXiv abstract URLs/IDs, rewritten to the PDF endpoint;
3. Unpaywall, when `--email` and a DOI are present.

Only open-access or clearly licensed PDFs are fetched. Paywalled articles are
recorded as `not_retrieved`; obtain those lawfully through your library — the
script does not bypass access controls.

Examples
--------
    # Save arXiv/open-access PDFs under inbox/<topic>/
    python fetch_articles.py sources/results.json --out inbox --topic "llm-refactoring"

    # Preview what would be downloaded, without writing files
    python fetch_articles.py sources/results.json --out inbox --topic tests --dry-run

    # Use Unpaywall for DOI records (polite pool needs a real contact address)
    python fetch_articles.py sources/results.json --out inbox \
      --topic refactoring --email you@example.org
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

USER_AGENT = "literature-review/1.0 (open-access retrieval; contact: {email})"
ARXIV_ABS = re.compile(r"arxiv\.org/(?:abs|pdf)/([A-Za-z0-9.\-]+?)(?:v\d+)?(?:\.pdf)?$")
ARXIV_ID = re.compile(r"^(?:arXiv:)?(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(?:v\d+)?$", re.I)
DEFAULT_TIMEOUT = 60
DEFAULT_SLEEP = 3.0
DEFAULT_MAX_BYTES = 100 * 1024 * 1024


class FetchError(RuntimeError):
    """A record could not be retrieved as an open-access PDF."""


def slugify(text: str, max_len: int = 80) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", decomposed).strip("-").lower()
    return (slug or "untitled")[:max_len].strip("-") or "untitled"


def load_records(path: Path) -> List[Dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FetchError(f"cannot read records from {path}: {exc}") from exc
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    if not isinstance(data, list):
        raise FetchError(f"{path} must contain a JSON array of record objects")
    return [r for r in data if isinstance(r, dict)]


def arxiv_pdf_url(record: Dict) -> Optional[str]:
    """Rewrite an arXiv abs/pdf URL or bare arXiv ID to the PDF endpoint."""
    for field in ("pdf_url", "open_access_pdf", "url", "doi"):
        value = str(record.get(field) or "").strip()
        if not value:
            continue
        match = ARXIV_ABS.search(value)
        if match:
            return f"https://arxiv.org/pdf/{match.group(1)}.pdf"
        bare = ARXIV_ID.match(value)
        if bare:
            return f"https://arxiv.org/pdf/{bare.group(1)}.pdf"
    return None


def unpaywall_pdf(doi: str, email: str, timeout: int) -> Optional[str]:
    doi = doi.strip().replace("https://doi.org/", "").replace("http://doi.org/", "")
    if not doi or not email:
        return None
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(email)}"
    payload = _read_url(url, timeout, expect_pdf=False)
    try:
        data = json.loads(payload.decode("utf-8", "replace"))
    except ValueError:
        return None
    location = data.get("best_oa_location") or {}
    return location.get("url_for_pdf") or location.get("url")


def resolve_pdf_url(record: Dict, email: str, timeout: int) -> Tuple[str, str]:
    """Return (pdf_url, source) or raise FetchError when none is available."""
    explicit = str(record.get("pdf_url") or record.get("open_access_pdf") or "").strip()
    if explicit:
        return explicit, "explicit"
    arxiv = arxiv_pdf_url(record)
    if arxiv:
        return arxiv, "arxiv"
    oa = unpaywall_pdf(str(record.get("doi") or ""), email, timeout)
    if oa:
        return oa, "unpaywall"
    raise FetchError("no open-access PDF URL (add pdf_url, an arXiv ID, or --email for Unpaywall)")


def _read_url(url: str, timeout: int, expect_pdf: bool, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT.format(email="anonymous")})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-supplied scholarly URLs
            ctype = (response.headers.get_content_type() or "").lower()
            data = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"request failed for {url}: {exc}") from exc
    if len(data) > max_bytes:
        raise FetchError(f"{url} exceeds {max_bytes} bytes")
    if expect_pdf and "pdf" not in ctype and not data[:5].lstrip().startswith(b"%PDF"):
        raise FetchError(f"{url} is not a PDF (content-type {ctype or 'unknown'})")
    return data


def download_pdf(url: str, destination: Path, timeout: int, overwrite: bool) -> str:
    if destination.exists() and not overwrite:
        return "exists"
    data = _read_url(url, timeout, expect_pdf=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return "downloaded"


def load_manifest(path: Path) -> Dict:
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("articles"), list):
                return data
        except (OSError, ValueError):
            pass
    return {"version": 1, "generated": "", "articles": []}


def save_manifest(path: Path, manifest: Dict) -> None:
    manifest["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    records = load_records(args.input)
    if args.max:
        records = records[: args.max]
    out_root = args.out
    manifest_path = out_root / "manifest.json"
    manifest = load_manifest(manifest_path)
    seen = {(a.get("doi") or "").lower() or (a.get("url") or "") for a in manifest["articles"]}

    counts = {"downloaded": 0, "exists": 0, "not_retrieved": 0, "disabled": 0}
    for index, record in enumerate(records, start=1):
        title = str(record.get("title") or "Untitled")
        doi = str(record.get("doi") or "").strip()
        url = str(record.get("url") or "").strip()
        key = doi.lower() or url
        target_dir = out_root / args.topic if args.topic else out_root
        filename = f"{index:03d}-{slugify(title)}.pdf"
        destination = target_dir / filename
        entry = {"title": title, "authors": record.get("authors", ""),
                 "year": str(record.get("year") or ""), "doi": doi, "url": url,
                 "path": "", "source": "", "status": "not_retrieved", "note": ""}
        if key and key in seen and not args.overwrite:
            entry["status"] = "exists"
            entry["note"] = "already in manifest"
            counts["exists"] += 1
        elif args.dry_run:
            entry["status"] = "disabled"
            entry["note"] = "dry-run"
            counts["disabled"] += 1
        else:
            try:
                pdf_url, source = resolve_pdf_url(record, args.email or "", args.timeout)
                entry["source"] = source
                status = download_pdf(pdf_url, destination, args.timeout, args.overwrite)
                entry["status"] = status
                entry["path"] = str(destination)
                counts[status] += 1
                if source == "arxiv":
                    time.sleep(args.sleep)
            except FetchError as exc:
                entry["status"] = "not_retrieved"
                entry["note"] = str(exc)
                counts["not_retrieved"] += 1
        manifest["articles"].append(entry)
        if key:
            seen.add(key)
        marker = {"downloaded": "+", "exists": "=", "not_retrieved": "!", "disabled": "."}[entry["status"]]
        print(f"[{marker}] {title[:70]} - {entry['status']}" + (f": {entry['note']}" if entry["note"] else ""))

    save_manifest(manifest_path, manifest)
    print(f"[OK] {counts} -> {manifest_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path,
                        help="Normalised search-results JSON array (title/url/doi/pdf_url fields)")
    parser.add_argument("--out", type=Path, required=True,
                        help="Destination root; PDFs are written here (or under --topic)")
    parser.add_argument("--topic", help="Subtopic subdirectory to save into")
    parser.add_argument("--email", help="Contact email for Unpaywall's polite pool")
    parser.add_argument("--max", type=int, help="Only process the first N records")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                        help="Seconds between arXiv requests (default 3)")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report without writing PDFs")
    parser.add_argument("--overwrite", action="store_true", help="Re-download existing files")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        raise SystemExit(run(args))
    except FetchError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
