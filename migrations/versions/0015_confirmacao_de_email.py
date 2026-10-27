"""usuarios.email_verificado_em e tokens_email: confirmação de e-mail e nova senha

Accounts created by the public sign-up confirm their e-mail before they can
use the app (auth.py, app/seguranca.py), and a forgotten password is replaced
through a link sent by e-mail. Accounts that exist now were all created by an
administrator (admin.py) or are the demo: they count as confirmed.

tokens_email holds the links' tokens (only their SHA-256). System table: no
tenant access, like sessoes.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("email_verificado_em", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE usuarios SET email_verificado_em = criado_em")

    op.create_table(
        "tokens_email",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column(
            "usuario_id", sa.BigInteger(), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("finalidade", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("finalidade IN ('confirmar_email', 'nova_senha')", name="ck_tokens_email_finalidade"),
    )
    op.create_index("ix_tokens_email_usuario_finalidade", "tokens_email", ["usuario_id", "finalidade"])


def downgrade() -> None:
    op.drop_index("ix_tokens_email_usuario_finalidade", table_name="tokens_email")
    op.drop_table("tokens_email")
    op.drop_column("usuarios", "email_verificado_em")
