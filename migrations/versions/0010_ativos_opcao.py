"""ativos: tipo opcao

Option trades from notas are stored under their own ativo type. Positions
and taxes leave them out for now (see loader and the Impostos page notice).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ANTES = ("'acao', 'fii', 'bdr', 'tesouro_direto', 'renda_fixa', "
          "'recibo_subscricao', 'direito_subscricao', 'desconhecido'")
_DEPOIS = ("'acao', 'fii', 'bdr', 'tesouro_direto', 'renda_fixa', "
           "'recibo_subscricao', 'direito_subscricao', 'opcao', 'desconhecido'")


def upgrade() -> None:
    op.drop_constraint(op.f("ck_ativos_tipo_valido"), "ativos", type_="check")
    op.create_check_constraint(op.f("ck_ativos_tipo_valido"), "ativos", f"tipo IN ({_DEPOIS})")


def downgrade() -> None:
    op.execute("UPDATE ativos SET tipo = 'desconhecido' WHERE tipo = 'opcao'")
    op.drop_constraint(op.f("ck_ativos_tipo_valido"), "ativos", type_="check")
    op.create_check_constraint(op.f("ck_ativos_tipo_valido"), "ativos", f"tipo IN ({_ANTES})")
