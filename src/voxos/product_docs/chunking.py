import hashlib
import re
from dataclasses import dataclass

from voxos.product_docs.loader import MarkdownDocument

TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
HEADING_LINE_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
CODE_FENCE_PATTERN = re.compile(r"^```")
LAST_UPDATED_LINE_PATTERN = re.compile(r"^Last updated:\s*\d{4}-\d{2}-\d{2}\s*$")


@dataclass(frozen=True, slots=True)
class MarkdownBlock:
    text: str
    kind: str
    heading_path: tuple[str, ...]
    heading_slug: str
    token_count: int


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    source_path: str
    chunk_index: int
    title: str
    heading_path: tuple[str, ...]
    heading_slug: str
    content: str
    content_hash: str
    token_count: int
    char_count: int

    @property
    def id(self) -> str:
        payload = f"{self.source_path}\0{self.chunk_index}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:64]


@dataclass(frozen=True, slots=True)
class MarkdownChunker:
    target_tokens: int = 700
    max_tokens: int = 900
    overlap_tokens: int = 80

    def chunk_document(self, document: MarkdownDocument) -> tuple[ChunkDraft, ...]:
        blocks = split_markdown_blocks(
            title=document.source.title,
            content=document.source.content,
        )
        chunks: list[ChunkDraft] = []
        current: list[MarkdownBlock] = []
        current_tokens = 0
        for block in blocks:
            block_parts = split_large_block(block=block, max_tokens=self.max_tokens)
            for part in block_parts:
                if (
                    current
                    and current_tokens + part.token_count > self.max_tokens
                    and has_content_block(tuple(current))
                ):
                    chunks.append(
                        build_chunk(
                            document=document,
                            chunk_index=len(chunks),
                            blocks=tuple(current),
                        ),
                    )
                    current = overlap_blocks(
                        blocks=tuple(current),
                        overlap_tokens=self.overlap_tokens,
                    )
                    current_tokens = sum(block.token_count for block in current)
                current.append(part)
                current_tokens += part.token_count
                if current_tokens >= self.target_tokens and has_content_block(tuple(current)):
                    chunks.append(
                        build_chunk(
                            document=document,
                            chunk_index=len(chunks),
                            blocks=tuple(current),
                        ),
                    )
                    current = overlap_blocks(
                        blocks=tuple(current),
                        overlap_tokens=self.overlap_tokens,
                    )
                    current_tokens = sum(block.token_count for block in current)

        if current and has_content_block(tuple(current)):
            chunks.append(
                build_chunk(
                    document=document,
                    chunk_index=len(chunks),
                    blocks=tuple(current),
                ),
            )
        return tuple(chunks)


def split_markdown_blocks(*, title: str, content: str) -> tuple[MarkdownBlock, ...]:
    lines = content.splitlines()
    blocks: list[MarkdownBlock] = []
    paragraph: list[str] = []
    heading_stack: list[str] = [title]
    current_slug = slug_for_heading(title)
    index = 0

    while index < len(lines):
        line = lines[index]
        if LAST_UPDATED_LINE_PATTERN.match(line):
            flush_paragraph(
                blocks=blocks,
                paragraph=paragraph,
                heading_path=tuple(heading_stack),
                heading_slug=current_slug,
            )
            index += 1
            continue

        heading_match = HEADING_LINE_PATTERN.match(line)
        if heading_match is not None:
            flush_paragraph(
                blocks=blocks,
                paragraph=paragraph,
                heading_path=tuple(heading_stack),
                heading_slug=current_slug,
            )
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            heading_stack = [*heading_stack[: level - 1], heading_text]
            current_slug = slug_for_heading(heading_text)
            blocks.append(
                build_block(
                    text=line,
                    kind="heading",
                    heading_path=tuple(heading_stack),
                    heading_slug=current_slug,
                ),
            )
            index += 1
            continue

        if CODE_FENCE_PATTERN.match(line):
            flush_paragraph(
                blocks=blocks,
                paragraph=paragraph,
                heading_path=tuple(heading_stack),
                heading_slug=current_slug,
            )
            fence_lines = [line]
            index += 1
            while index < len(lines):
                fence_lines.append(lines[index])
                if CODE_FENCE_PATTERN.match(lines[index]):
                    index += 1
                    break
                index += 1
            blocks.append(
                build_block(
                    text="\n".join(fence_lines),
                    kind="code",
                    heading_path=tuple(heading_stack),
                    heading_slug=current_slug,
                ),
            )
            continue

        if is_table_line(line):
            flush_paragraph(
                blocks=blocks,
                paragraph=paragraph,
                heading_path=tuple(heading_stack),
                heading_slug=current_slug,
            )
            table_lines = [line]
            index += 1
            while index < len(lines) and is_table_line(lines[index]):
                table_lines.append(lines[index])
                index += 1
            blocks.append(
                build_block(
                    text="\n".join(table_lines),
                    kind="table",
                    heading_path=tuple(heading_stack),
                    heading_slug=current_slug,
                ),
            )
            continue

        if not line.strip():
            flush_paragraph(
                blocks=blocks,
                paragraph=paragraph,
                heading_path=tuple(heading_stack),
                heading_slug=current_slug,
            )
            index += 1
            continue

        paragraph.append(line)
        index += 1

    flush_paragraph(
        blocks=blocks,
        paragraph=paragraph,
        heading_path=tuple(heading_stack),
        heading_slug=current_slug,
    )
    return tuple(blocks)


def flush_paragraph(
    *,
    blocks: list[MarkdownBlock],
    paragraph: list[str],
    heading_path: tuple[str, ...],
    heading_slug: str,
) -> None:
    if not paragraph:
        return
    blocks.append(
        build_block(
            text="\n".join(paragraph),
            kind="paragraph",
            heading_path=heading_path,
            heading_slug=heading_slug,
        ),
    )
    paragraph.clear()


def build_block(
    *,
    text: str,
    kind: str,
    heading_path: tuple[str, ...],
    heading_slug: str,
) -> MarkdownBlock:
    stripped_text = text.strip()
    return MarkdownBlock(
        text=stripped_text,
        kind=kind,
        heading_path=heading_path,
        heading_slug=heading_slug,
        token_count=max(1, count_tokens(stripped_text)),
    )


def split_large_block(
    *,
    block: MarkdownBlock,
    max_tokens: int,
) -> tuple[MarkdownBlock, ...]:
    if block.token_count <= max_tokens or block.kind in {"code", "table"}:
        return (block,)
    words = block.text.split()
    parts: list[MarkdownBlock] = []
    current: list[str] = []
    current_tokens = 0
    for word in words:
        word_tokens = count_tokens(word)
        if current and current_tokens + word_tokens > max_tokens:
            parts.append(
                build_block(
                    text=" ".join(current),
                    kind=block.kind,
                    heading_path=block.heading_path,
                    heading_slug=block.heading_slug,
                ),
            )
            current = []
            current_tokens = 0
        current.append(word)
        current_tokens += word_tokens
    if current:
        parts.append(
            build_block(
                text=" ".join(current),
                kind=block.kind,
                heading_path=block.heading_path,
                heading_slug=block.heading_slug,
            ),
        )
    return tuple(parts)


def overlap_blocks(
    *,
    blocks: tuple[MarkdownBlock, ...],
    overlap_tokens: int,
) -> list[MarkdownBlock]:
    if overlap_tokens <= 0:
        return []
    selected: list[MarkdownBlock] = []
    token_count = 0
    for block in reversed(blocks):
        if block.token_count > overlap_tokens:
            continue
        selected.append(block)
        token_count += block.token_count
        if token_count >= overlap_tokens:
            break
    selected.reverse()
    return selected


def has_content_block(blocks: tuple[MarkdownBlock, ...]) -> bool:
    return any(block.kind != "heading" for block in blocks)


def build_chunk(
    *,
    document: MarkdownDocument,
    chunk_index: int,
    blocks: tuple[MarkdownBlock, ...],
) -> ChunkDraft:
    content = "\n\n".join(block.text for block in blocks).strip()
    heading_block = first_content_block(blocks)
    heading_path = heading_block.heading_path if heading_block is not None else (document.source.title,)
    heading_slug = (
        heading_block.heading_slug
        if heading_block is not None
        else slug_for_heading(
            document.source.title,
        )
    )
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return ChunkDraft(
        source_path=document.source.path,
        chunk_index=chunk_index,
        title=document.source.title,
        heading_path=heading_path,
        heading_slug=heading_slug,
        content=content,
        content_hash=content_hash,
        token_count=max(1, count_tokens(content)),
        char_count=len(content),
    )


def first_content_block(blocks: tuple[MarkdownBlock, ...]) -> MarkdownBlock | None:
    for block in blocks:
        if block.kind != "heading":
            return block
    return blocks[0] if blocks else None


def is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def count_tokens(text: str) -> int:
    return len(TOKEN_PATTERN.findall(text))


def slug_for_heading(heading: str) -> str:
    normalized = re.sub(r"[^\w\s-]", "", heading.lower(), flags=re.UNICODE)
    normalized = re.sub(r"\s+", "-", normalized.strip())
    return normalized or "section"
