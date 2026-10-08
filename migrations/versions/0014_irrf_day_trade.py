"""notas.irrf_day_trade: IRRF de day trade lido da nota

The market-standard layout prints the 1% withheld on the day's day-trade
result ("IRRF Day-Trade: Base R$ ... Projeção R$ ..."). Notas loaded before
this column have NULL: the tax page then estimates it.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notas", sa.Column("irrf_day_trade", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("notas", "irrf_day_trade")
