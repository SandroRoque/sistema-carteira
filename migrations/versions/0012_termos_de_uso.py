"""usuarios: aceite dos termos de uso

Each account must accept the current version of the terms of use (auth.TERMOS_VERSAO)
before using the app; the version and the moment are kept.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("termos_versao", sa.Text(), nullable=True))
    op.add_column("usuarios", sa.Column("termos_aceitos_em", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("usuarios", "termos_aceitos_em")
    op.drop_column("usuarios", "termos_versao")
