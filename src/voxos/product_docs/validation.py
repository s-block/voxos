import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence, cast
from urllib.parse import unquote, urlparse

import orjson
from pydantic import BaseModel, ConfigDict, Field

MANIFEST_FILENAME = "docs-manifest.json"
REQUIRED_WIKI_FILES = ("Home.md", "_Sidebar.md")
TITLE_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
LAST_UPDATED_PATTERN = re.compile(r"^Last updated:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
MARKDOWN_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class MarkdownLink:
    target: str
    line_number: int


@dataclass(frozen=True, slots=True)
class WikiPage:
    path: Path
    relative_path: str
    title: str
    source_hash: str
    last_updated: date
    anchors: frozenset[str]
    links: tuple[MarkdownLink, ...]
    content: str


class ManifestPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=19, max_length=19)
    last_updated: date


class DocsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generated_at: datetime
    docs_dir: str = Field(min_length=1)
    pages: tuple[ManifestPage, ...] = Field(default_factory=tuple)


def load_wiki_pages(docs_dir: Path) -> tuple[WikiPage, ...]:
    pages: list[WikiPage] = []
    for path in sorted(docs_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(docs_dir).as_posix()
        title_match = TITLE_PATTERN.search(content)
        if title_match is None:
            title = path.stem
        else:
            title = title_match.group(1).strip()

        last_updated_match = LAST_UPDATED_PATTERN.search(content)
        last_updated = date.min if last_updated_match is None else date.fromisoformat(last_updated_match.group(1))
        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        pages.append(
            WikiPage(
                path=path,
                relative_path=relative_path,
                title=title,
                source_hash=source_hash,
                last_updated=last_updated,
                anchors=anchors_for_content(content),
                links=links_for_content(content),
                content=content,
            ),
        )
    return tuple(pages)


def validate_wiki_docs(
    docs_dir: Path,
    *,
    check_manifest: bool = True,
) -> tuple[str, ...]:
    errors: list[str] = []
    if not docs_dir.exists():
        return (f"Docs directory does not exist: {docs_dir}",)
    if not docs_dir.is_dir():
        return (f"Docs path is not a directory: {docs_dir}",)

    pages = load_wiki_pages(docs_dir)
    page_by_relative_path = {page.relative_path: page for page in pages}
    titles: dict[str, str] = {}

    for required_file in REQUIRED_WIKI_FILES:
        if required_file not in page_by_relative_path:
            errors.append(f"Missing required wiki file: {required_file}")

    for page in pages:
        if page.title in titles:
            errors.append(
                f"Duplicate title '{page.title}' in {titles[page.title]} and {page.relative_path}",
            )
        titles[page.title] = page.relative_path
        if page.last_updated == date.min:
            errors.append(f"Missing Last updated metadata: {page.relative_path}")
        errors.extend(validate_links(page=page, docs_dir=docs_dir))

    if check_manifest:
        errors.extend(validate_manifest(docs_dir=docs_dir, pages=pages))
    return tuple(errors)


def generate_manifest(docs_dir: Path) -> DocsManifest:
    pages = load_wiki_pages(docs_dir)
    return DocsManifest(
        generated_at=datetime.now(UTC),
        docs_dir=docs_dir.as_posix(),
        pages=tuple(
            ManifestPage(
                path=page.relative_path,
                title=page.title,
                source_fingerprint=source_fingerprint(page.source_hash),
                last_updated=page.last_updated,
            )
            for page in pages
        ),
    )


def write_manifest(docs_dir: Path) -> Path:
    manifest = generate_manifest(docs_dir)
    manifest_path = docs_dir / MANIFEST_FILENAME
    manifest_path.write_bytes(
        orjson.dumps(
            manifest.model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
        )
        + b"\n",
    )
    return manifest_path


def read_manifest(docs_dir: Path) -> DocsManifest | None:
    manifest_path = docs_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None
    payload = cast(dict[str, object], orjson.loads(manifest_path.read_bytes()))
    return DocsManifest.model_validate(payload)


def validate_manifest(
    *,
    docs_dir: Path,
    pages: tuple[WikiPage, ...],
) -> tuple[str, ...]:
    manifest = read_manifest(docs_dir)
    if manifest is None:
        return (f"Missing docs manifest: {docs_dir / MANIFEST_FILENAME}",)
    expected_pages = {
        page.relative_path: ManifestPage(
            path=page.relative_path,
            title=page.title,
            source_fingerprint=source_fingerprint(page.source_hash),
            last_updated=page.last_updated,
        )
        for page in pages
    }
    actual_pages = {page.path: page for page in manifest.pages}

    errors: list[str] = []
    for path, expected_page in expected_pages.items():
        actual_page = actual_pages.get(path)
        if actual_page is None:
            errors.append(f"Manifest missing page: {path}")
            continue
        if actual_page != expected_page:
            errors.append(f"Manifest stale for page: {path}")
    for path in actual_pages:
        if path not in expected_pages:
            errors.append(f"Manifest contains removed page: {path}")
    return tuple(errors)


def validate_links(*, page: WikiPage, docs_dir: Path) -> tuple[str, ...]:
    errors: list[str] = []
    for link in page.links:
        target = link.target.strip()
        if not target or is_external_target(target):
            continue
        target_path_text, _, anchor = target.partition("#")
        if not target_path_text:
            if anchor and slug_for_heading(unquote(anchor)) not in page.anchors:
                errors.append(
                    f"{page.relative_path}:{link.line_number} broken anchor: {target}",
                )
            continue

        target_path = (page.path.parent / unquote(target_path_text)).resolve()
        try:
            target_path.relative_to(docs_dir.parent.resolve())
        except ValueError:
            errors.append(
                f"{page.relative_path}:{link.line_number} link escapes docs tree: {target}",
            )
            continue
        if not target_path.exists():
            errors.append(f"{page.relative_path}:{link.line_number} missing link target: {target}")
            continue
        if anchor and target_path.suffix == ".md":
            target_content = target_path.read_text(encoding="utf-8")
            if slug_for_heading(unquote(anchor)) not in anchors_for_content(target_content):
                errors.append(
                    f"{page.relative_path}:{link.line_number} broken target anchor: {target}",
                )
    return tuple(errors)


def links_for_content(content: str) -> tuple[MarkdownLink, ...]:
    links: list[MarkdownLink] = []
    for match in MARKDOWN_LINK_PATTERN.finditer(content):
        line_number = content.count("\n", 0, match.start()) + 1
        links.append(MarkdownLink(target=match.group(1), line_number=line_number))
    return tuple(links)


def anchors_for_content(content: str) -> frozenset[str]:
    anchors = {"top"}
    for match in HEADING_PATTERN.finditer(content):
        anchors.add(slug_for_heading(match.group(2)))
    return frozenset(anchors)


def slug_for_heading(heading: str) -> str:
    normalized = re.sub(r"[^\w\s-]", "", heading.lower(), flags=re.UNICODE)
    normalized = re.sub(r"\s+", "-", normalized.strip())
    return normalized or "section"


def source_fingerprint(source_hash: str) -> str:
    prefix = source_hash[:16]
    return "-".join(prefix[index : index + 4] for index in range(0, len(prefix), 4))


def is_external_target(target: str) -> bool:
    parsed = urlparse(target)
    return parsed.scheme in {"http", "https", "mailto"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Voxos wiki docs.")
    parser.add_argument("--docs-dir", default="docs/wiki", help="Wiki docs directory.")
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Regenerate docs-manifest.json before validation.",
    )
    parser.add_argument(
        "--check-manifest",
        action="store_true",
        help="Require docs-manifest.json to match current pages.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    docs_dir = Path(args.docs_dir)
    if args.write_manifest:
        write_manifest(docs_dir)
    errors = validate_wiki_docs(
        docs_dir,
        check_manifest=args.check_manifest or not args.write_manifest,
    )
    if errors:
        for error in errors:
            sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"Validated wiki docs in {docs_dir}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
