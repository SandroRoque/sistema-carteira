"""conta de demonstracao

Flags the public demo account (demo.py). At most one account has it.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("demo", sa.Boolean(), server_default="false", nullable=False))
    op.create_index(
        "uq_usuarios_demo", "usuarios", ["demo"], unique=True, postgresql_where=sa.text("demo")
    )


def downgrade() -> None:
    op.drop_index("uq_usuarios_demo", table_name="usuarios", postgresql_where=sa.text("demo"))
    op.drop_column("usuarios", "demo")
