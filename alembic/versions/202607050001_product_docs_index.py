"""add product docs index

Revision ID: 202607050001
Revises: 202606280001
Create Date: 2026-07-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection
from sqlalchemy.types import UserDefinedType

revision: str = "202607050001"
down_revision: str | None = "202606280001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PGVECTOR_EXTENSION_REQUIRED = (
    "PostgreSQL extension 'vector' must be enabled in the target database "
    "before applying migration 202607050001. The pgvector package can be "
    "installed on the server while the extension is still missing from this "
    "database. Run `make db-enable-vector` with local database admin settings, "
    "then retry `uv run alembic upgrade head`."
)


class VectorType(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: object) -> str:
        return f"vector({self.dimensions})"


def upgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"
    if is_postgresql:
        require_pgvector_extension(bind)
    embedding_type = VectorType(1536) if is_postgresql else sa.JSON()

    op.create_table(
        "product_doc_sources",
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("last_updated", sa.Date(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("path"),
    )
    op.create_index(
        "ix_product_doc_sources_indexed_at",
        "product_doc_sources",
        ["indexed_at"],
    )
    op.create_index(
        "ix_product_doc_sources_last_updated",
        "product_doc_sources",
        ["last_updated"],
    )
    op.create_index(
        "ix_product_doc_sources_source_hash",
        "product_doc_sources",
        ["source_hash"],
    )

    op.create_table(
        "product_doc_chunks",
        sa.Column("id", sa.String(length=96), nullable=False),
        sa.Column("source_path", sa.String(length=512), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("heading_path", sa.JSON(), nullable=False),
        sa.Column("heading_slug", sa.String(length=256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_path"], ["product_doc_sources.path"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_path",
            "chunk_index",
            name="uq_product_doc_chunks_source_index",
        ),
    )
    op.create_index(
        "ix_product_doc_chunks_content_hash",
        "product_doc_chunks",
        ["content_hash"],
    )
    op.create_index(
        "ix_product_doc_chunks_embedding_model",
        "product_doc_chunks",
        ["embedding_model"],
    )
    op.create_index(
        "ix_product_doc_chunks_source_path",
        "product_doc_chunks",
        ["source_path"],
    )
    op.create_index(
        "ix_product_doc_chunks_updated_at",
        "product_doc_chunks",
        ["updated_at"],
    )
    if is_postgresql:
        op.execute(
            """
            CREATE INDEX ix_product_doc_chunks_embedding_hnsw
            ON product_doc_chunks
            USING hnsw (embedding vector_cosine_ops)
            """,
        )
        op.execute(
            """
            CREATE INDEX ix_product_doc_chunks_fts
            ON product_doc_chunks
            USING gin (
                to_tsvector(
                    'english',
                    title || ' ' || heading_path::text || ' ' || content
                )
            )
            """,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_product_doc_chunks_fts")
        op.execute("DROP INDEX IF EXISTS ix_product_doc_chunks_embedding_hnsw")
    op.drop_index("ix_product_doc_chunks_updated_at", table_name="product_doc_chunks")
    op.drop_index("ix_product_doc_chunks_source_path", table_name="product_doc_chunks")
    op.drop_index("ix_product_doc_chunks_embedding_model", table_name="product_doc_chunks")
    op.drop_index("ix_product_doc_chunks_content_hash", table_name="product_doc_chunks")
    op.drop_table("product_doc_chunks")
    op.drop_index("ix_product_doc_sources_source_hash", table_name="product_doc_sources")
    op.drop_index("ix_product_doc_sources_last_updated", table_name="product_doc_sources")
    op.drop_index("ix_product_doc_sources_indexed_at", table_name="product_doc_sources")
    op.drop_table("product_doc_sources")


def require_pgvector_extension(bind: Connection) -> None:
    available = bool(
        bind.execute(
            sa.text(
                """
                SELECT to_regtype('vector') IS NOT NULL
                """,
            ),
        ).scalar_one(),
    )
    if not available:
        raise RuntimeError(PGVECTOR_EXTENSION_REQUIRED)
