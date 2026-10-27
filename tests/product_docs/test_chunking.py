from datetime import UTC, date, datetime

from voxos.product_docs.chunking import MarkdownChunker
from voxos.product_docs.loader import MarkdownDocument
from voxos.product_docs.models import ProductDocSource


def test_chunker_preserves_code_fences_and_tables() -> None:
    document = MarkdownDocument(
        source=ProductDocSource(
            path="Example.md",
            title="Example",
            content=(
                "# Example\n\n"
                "Last updated: 2026-07-05\n\n"
                "## Setup\n\n"
                "Use this command:\n\n"
                "```bash\n"
                "make docs-validate\n"
                "```\n\n"
                "| Name | Value |\n"
                "| --- | --- |\n"
                "| API | /health |\n"
            ),
            source_hash="a" * 64,
            last_updated=date(2026, 7, 5),
            indexed_at=datetime(2026, 7, 5, tzinfo=UTC),
        ),
    )

    chunks = MarkdownChunker(target_tokens=40, max_tokens=80).chunk_document(document)

    assert chunks
    assert any("```bash\nmake docs-validate\n```" in chunk.content for chunk in chunks)
    assert any("| API | /health |" in chunk.content for chunk in chunks)
    assert chunks[0].heading_path == ("Example", "Setup")
    assert (
        chunks[0].id
        == MarkdownChunker(target_tokens=40, max_tokens=80)
        .chunk_document(
            document,
        )[0]
        .id
    )


def test_chunker_splits_large_paragraph_with_stable_metadata() -> None:
    long_text = " ".join(f"word-{index}" for index in range(180))
    document = MarkdownDocument(
        source=ProductDocSource(
            path="Long.md",
            title="Long",
            content=f"# Long\n\nLast updated: 2026-07-05\n\n## Details\n\n{long_text}",
            source_hash="b" * 64,
            last_updated=date(2026, 7, 5),
            indexed_at=datetime(2026, 7, 5, tzinfo=UTC),
        ),
    )

    chunks = MarkdownChunker(target_tokens=70, max_tokens=90, overlap_tokens=10).chunk_document(
        document,
    )

    assert len(chunks) > 1
    assert all(chunk.heading_path == ("Long", "Details") for chunk in chunks)
    assert all(chunk.token_count <= 100 for chunk in chunks)


def test_chunker_keeps_logical_chunk_id_when_content_changes() -> None:
    original = document_with_content("Use MCP.")
    updated = document_with_content("Use LiveKit.")

    original_chunk = MarkdownChunker().chunk_document(original)[0]
    updated_chunk = MarkdownChunker().chunk_document(updated)[0]

    assert original_chunk.id == updated_chunk.id
    assert original_chunk.content_hash != updated_chunk.content_hash


def document_with_content(content: str) -> MarkdownDocument:
    return MarkdownDocument(
        source=ProductDocSource(
            path="Stable.md",
            title="Stable",
            content=f"# Stable\n\nLast updated: 2026-07-05\n\n## Setup\n\n{content}",
            source_hash="c" * 64,
            last_updated=date(2026, 7, 5),
            indexed_at=datetime(2026, 7, 5, tzinfo=UTC),
        ),
    )
