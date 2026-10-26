from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ProductDocSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)
    source_hash: str = Field(min_length=64, max_length=64)
    last_updated: date
    indexed_at: datetime


class ProductDocChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=96)
    source_path: str = Field(min_length=1, max_length=512)
    chunk_index: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=256)
    heading_path: tuple[str, ...] = Field(default_factory=tuple)
    heading_slug: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    token_count: int = Field(ge=1)
    char_count: int = Field(ge=1)
    embedding_model: str = Field(min_length=1, max_length=128)
    embedding_dimension: int = Field(ge=1)
    embedding: tuple[float, ...] = Field(min_length=1)
    updated_at: datetime


class ProductDocSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(
        min_length=1,
        max_length=512,
        description="Words to find in indexed Voxos product documentation.",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of matching documentation excerpts to return.",
    )
    source_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Optional Voxos wiki page path to search within.",
    )


class ProductDocSearchMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1, max_length=96)
    title: str = Field(min_length=1, max_length=256)
    source_path: str = Field(min_length=1, max_length=512)
    heading_path: tuple[str, ...] = Field(default_factory=tuple)
    heading_slug: str = Field(min_length=1, max_length=256)
    excerpt: str = Field(min_length=1)
    score: float = Field(ge=0)
    vector_score: float = Field(ge=0)
    text_score: float = Field(ge=0)


class ProductDocSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    matches: tuple[ProductDocSearchMatch, ...] = Field(default_factory=tuple)
    message: str | None = Field(default=None, min_length=1, max_length=512)


class ProductDocPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=256)
    source_path: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)
    source_hash: str = Field(min_length=64, max_length=64)
    last_updated: date


class ProductDocPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: ProductDocPage | None = None
    message: str | None = Field(default=None, min_length=1, max_length=512)


class ProductDocsIndexStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sources_seen: int = Field(ge=0)
    sources_upserted: int = Field(ge=0)
    sources_pruned: int = Field(default=0, ge=0)
    chunks_seen: int = Field(ge=0)
    chunks_upserted: int = Field(ge=0)
    chunks_pruned: int = Field(ge=0)
    dry_run: bool = False
