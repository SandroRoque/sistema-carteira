"""darfs pagos

DARFs (monthly variable-income tax) the user marked as paid. Tenant-owned
with RLS like the other portfolio tables; the web role reads and writes it.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USUARIO_ATUAL = "NULLIF(current_setting('app.usuario_id', true), '')::bigint"
_POLITICA = f"investidor_id IN (SELECT id FROM investidores WHERE usuario_id = {_USUARIO_ATUAL})"


def upgrade() -> None:
    op.create_table(
        "darfs_pagos",
        sa.Column("investidor_id", sa.BigInteger(), nullable=False),
        sa.Column("mes", sa.Date(), nullable=False),
        sa.Column("valor_pago", sa.Numeric(), nullable=False),
        sa.Column("pago_em", sa.Date(), nullable=False),
        sa.CheckConstraint("EXTRACT(DAY FROM mes) = 1", name=op.f("ck_darfs_pagos_mes_primeiro_dia")),
        sa.CheckConstraint("valor_pago >= 0", name=op.f("ck_darfs_pagos_valor_nao_negativo")),
        sa.ForeignKeyConstraint(
            ["investidor_id"], ["investidores.id"],
            name=op.f("fk_darfs_pagos_investidor_id_investidores"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("investidor_id", "mes", name=op.f("pk_darfs_pagos")),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON darfs_pagos TO carteira_app")
    op.execute("ALTER TABLE darfs_pagos ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY darfs_pagos_por_usuario ON darfs_pagos USING ({_POLITICA}) WITH CHECK ({_POLITICA})")


def downgrade() -> None:
    op.drop_table("darfs_pagos")
