"""falhas_login_ip: limite de tentativas de login por IP

Failed logins are counted per client IP (kept as an HMAC) as well as per
account, so one password tried against many e-mails is throttled too.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "falhas_login_ip",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("ip_hash", sa.Text(), nullable=False),
        sa.Column("em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_falhas_login_ip_ip_hash_em", "falhas_login_ip", ["ip_hash", "em"])


def downgrade() -> None:
    op.drop_index("ix_falhas_login_ip_ip_hash_em", table_name="falhas_login_ip")
    op.drop_table("falhas_login_ip")
