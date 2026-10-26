from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from voxos.product_docs.models import ProductDocSource
from voxos.product_docs.validation import WikiPage, load_wiki_pages


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    source: ProductDocSource


def load_markdown_documents(docs_dir: Path) -> tuple[MarkdownDocument, ...]:
    documents: list[MarkdownDocument] = []
    indexed_at = datetime.now(UTC)
    for page in load_wiki_pages(docs_dir):
        if should_index_page(page):
            documents.append(
                MarkdownDocument(
                    source=ProductDocSource(
                        path=page.relative_path,
                        title=page.title,
                        content=page.content,
                        source_hash=page.source_hash,
                        last_updated=page.last_updated,
                        indexed_at=indexed_at,
                    ),
                ),
            )
    return tuple(documents)


def should_index_page(page: WikiPage) -> bool:
    return not page.relative_path.startswith("_")
