"""custos informados

Cost per share the user informs for shares received without a trade note
(custody transfers, B3 credits). Tenant-owned: RLS like the other
portfolio tables (migration 0003); the web role reads and writes it.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USUARIO_ATUAL = "NULLIF(current_setting('app.usuario_id', true), '')::bigint"
_POLITICA = f"investidor_id IN (SELECT id FROM investidores WHERE usuario_id = {_USUARIO_ATUAL})"


def upgrade() -> None:
    op.create_table(
        "custos_informados",
        sa.Column("investidor_id", sa.BigInteger(), nullable=False),
        sa.Column("ativo_id", sa.BigInteger(), nullable=False),
        sa.Column("custo_por_cota", sa.Numeric(), nullable=False),
        sa.Column("informado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("custo_por_cota >= 0", name=op.f("ck_custos_informados_custo_nao_negativo")),
        sa.ForeignKeyConstraint(
            ["ativo_id"], ["ativos.id"], name=op.f("fk_custos_informados_ativo_id_ativos"),
        ),
        sa.ForeignKeyConstraint(
            ["investidor_id"], ["investidores.id"],
            name=op.f("fk_custos_informados_investidor_id_investidores"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("investidor_id", "ativo_id", name=op.f("pk_custos_informados")),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON custos_informados TO carteira_app")
    op.execute("ALTER TABLE custos_informados ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY custos_informados_por_usuario ON custos_informados "
        f"USING ({_POLITICA}) WITH CHECK ({_POLITICA})"
    )


def downgrade() -> None:
    op.drop_table("custos_informados")
