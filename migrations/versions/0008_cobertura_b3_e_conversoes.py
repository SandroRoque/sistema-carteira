"""cobertura b3 e conversoes

b3_arquivos_processados gains the period each report covers (backfilled
from the rows attributed to it). conversoes records B3 credits the user
confirmed as replacing another ativo (incorporação, conversão); it is
tenant-owned with RLS like the other portfolio tables.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USUARIO_ATUAL = "NULLIF(current_setting('app.usuario_id', true), '')::bigint"
_POLITICA = f"investidor_id IN (SELECT id FROM investidores WHERE usuario_id = {_USUARIO_ATUAL})"


def upgrade() -> None:
    op.add_column("b3_arquivos_processados", sa.Column("periodo_inicio", sa.Date(), nullable=True))
    op.add_column("b3_arquivos_processados", sa.Column("periodo_fim", sa.Date(), nullable=True))
    op.execute(
        """
        UPDATE b3_arquivos_processados a
        SET periodo_inicio = p.inicio, periodo_fim = p.fim
        FROM (SELECT arquivo_id, MIN(data) AS inicio, MAX(data) AS fim
              FROM b3_movimentacoes GROUP BY arquivo_id) p
        WHERE p.arquivo_id = a.id
        """
    )

    op.create_table(
        "conversoes",
        sa.Column("b3_movimentacao_id", sa.BigInteger(), nullable=False),
        sa.Column("investidor_id", sa.BigInteger(), nullable=False),
        sa.Column("ativo_origem_id", sa.BigInteger(), nullable=False),
        sa.Column("informado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["b3_movimentacao_id"], ["b3_movimentacoes.id"],
            name=op.f("fk_conversoes_b3_movimentacao_id_b3_movimentacoes"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["investidor_id"], ["investidores.id"],
            name=op.f("fk_conversoes_investidor_id_investidores"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ativo_origem_id"], ["ativos.id"], name=op.f("fk_conversoes_ativo_origem_id_ativos"),
        ),
        sa.PrimaryKeyConstraint("b3_movimentacao_id", name=op.f("pk_conversoes")),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON conversoes TO carteira_app")
    op.execute("ALTER TABLE conversoes ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY conversoes_por_usuario ON conversoes "
        f"USING ({_POLITICA}) WITH CHECK ({_POLITICA})"
    )


def downgrade() -> None:
    op.drop_table("conversoes")
    op.drop_column("b3_arquivos_processados", "periodo_fim")
    op.drop_column("b3_arquivos_processados", "periodo_inicio")
