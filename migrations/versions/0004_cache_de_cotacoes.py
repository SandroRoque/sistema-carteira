"""cache de cotacoes

Shared market price cache: each ticker is downloaded at most once per
validity window, whichever account asks (cotacoes.py). Holds no tenant data,
so carteira_app reads and writes it without a row-level policy.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cotacoes",
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("preco", sa.Numeric(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("ticker", name=op.f("pk_cotacoes")),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON cotacoes TO carteira_app")


def downgrade() -> None:
    op.drop_table("cotacoes")
