from datetime import date, datetime
from typing import cast

from sqlalchemy import Column, Date, DateTime, Index, Integer, Text, UniqueConstraint
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import JSON, TypeDecorator, TypeEngine, UserDefinedType
from sqlmodel import Field, SQLModel

from voxos.db.models import utc_now

DEFAULT_EMBEDDING_DIMENSION = 1536


class PostgresVectorType(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: object) -> str:
        if self.dimensions is None:
            return "vector"
        return f"vector({self.dimensions})"


class VectorType(TypeDecorator[list[float]]):
    impl = JSON
    cache_ok = True

    def __init__(self, dimensions: int | None = DEFAULT_EMBEDDING_DIMENSION) -> None:
        super().__init__()
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[object]:
        if dialect.name == "postgresql":
            return cast(TypeEngine[object], PostgresVectorType(self.dimensions))
        return cast(TypeEngine[object], dialect.type_descriptor(JSON()))

    def process_bind_param(
        self,
        value: list[float] | tuple[float, ...] | None,
        dialect: Dialect,
    ) -> list[float] | str | None:
        if value is None:
            return None
        vector = [float(item) for item in value]
        if dialect.name == "postgresql":
            return vector_to_pg_text(tuple(vector))
        return vector

    def process_result_value(
        self,
        value: object,
        dialect: Dialect,
    ) -> list[float]:
        if value is None:
            return []
        if isinstance(value, str):
            return vector_from_pg_text(value)
        if isinstance(value, list | tuple):
            return [float(item) for item in value]
        message = f"Unsupported vector value from {dialect.name}: {type(value).__name__}"
        raise TypeError(message)


class ProductDocSourceRecord(SQLModel, table=True):
    __tablename__ = "product_doc_sources"
    __table_args__ = (
        Index("ix_product_doc_sources_last_updated", "last_updated"),
        Index("ix_product_doc_sources_indexed_at", "indexed_at"),
    )

    path: str = Field(primary_key=True, min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=256)
    content: str = Field(sa_column=Column(Text, nullable=False))
    source_hash: str = Field(min_length=64, max_length=64, index=True)
    last_updated: date = Field(sa_column=Column(Date, nullable=False))
    indexed_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class ProductDocChunkRecord(SQLModel, table=True):
    __tablename__ = "product_doc_chunks"
    __table_args__ = (
        UniqueConstraint(
            "source_path",
            "chunk_index",
            name="uq_product_doc_chunks_source_index",
        ),
        Index("ix_product_doc_chunks_source_path", "source_path"),
        Index("ix_product_doc_chunks_embedding_model", "embedding_model"),
        Index("ix_product_doc_chunks_updated_at", "updated_at"),
    )

    id: str = Field(primary_key=True, min_length=1, max_length=96)
    source_path: str = Field(
        foreign_key="product_doc_sources.path",
        min_length=1,
        max_length=512,
    )
    chunk_index: int = Field(sa_column=Column(Integer, nullable=False))
    title: str = Field(min_length=1, max_length=256)
    heading_path: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    heading_slug: str = Field(min_length=1, max_length=256)
    content: str = Field(sa_column=Column(Text, nullable=False))
    content_hash: str = Field(min_length=64, max_length=64, index=True)
    token_count: int = Field(sa_column=Column(Integer, nullable=False))
    char_count: int = Field(sa_column=Column(Integer, nullable=False))
    embedding_model: str = Field(min_length=1, max_length=128)
    embedding_dimension: int = Field(sa_column=Column(Integer, nullable=False))
    embedding: list[float] = Field(
        default_factory=list,
        sa_column=Column(VectorType(), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


def vector_to_pg_text(vector: tuple[float, ...]) -> str:
    return f"[{','.join(format(value, '.12g') for value in vector)}]"


def vector_from_pg_text(value: str) -> list[float]:
    stripped = value.strip().removeprefix("[").removesuffix("]")
    if not stripped:
        return []
    return [float(item) for item in stripped.split(",")]
