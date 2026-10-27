"""ativos: tipos etf e fundo; tickers terminados em 11 reclassificados

A ticker ending in 11 used to be taken as a FII. dados/classes_b3.csv
(built from B3's public data, see classes_b3.py) tells units, ETFs, Fiagros
and other listed funds apart; catalog rows nobody has reviewed take its class.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import classes_b3

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ANTES = ("'acao', 'fii', 'bdr', 'tesouro_direto', 'renda_fixa', "
          "'recibo_subscricao', 'direito_subscricao', 'opcao', 'desconhecido'")
_DEPOIS = ("'acao', 'fii', 'bdr', 'etf', 'fundo', 'tesouro_direto', 'renda_fixa', "
           "'recibo_subscricao', 'direito_subscricao', 'opcao', 'desconhecido'")


def upgrade() -> None:
    op.drop_constraint(op.f("ck_ativos_tipo_valido"), "ativos", type_="check")
    op.create_check_constraint(op.f("ck_ativos_tipo_valido"), "ativos", f"tipo IN ({_DEPOIS})")
    conn = op.get_bind()
    linhas = conn.execute(sa.text(
        "SELECT id, ticker FROM ativos WHERE NOT revisado AND tipo IN ('fii', 'acao') AND ticker LIKE '%11'"
    )).all()
    for ativo_id, ticker in linhas:
        classe = classes_b3.classe(ticker)
        if classe:
            conn.execute(
                sa.text("UPDATE ativos SET tipo = :tipo, subtipo = :subtipo WHERE id = :id"),
                {"tipo": classe[0], "subtipo": classe[1], "id": ativo_id},
            )


def downgrade() -> None:
    op.execute("UPDATE ativos SET tipo = 'fii', subtipo = NULL WHERE tipo IN ('etf', 'fundo')")
    op.execute("UPDATE ativos SET subtipo = NULL WHERE subtipo IN ('unit', 'fiagro')")
    op.drop_constraint(op.f("ck_ativos_tipo_valido"), "ativos", type_="check")
    op.create_check_constraint(op.f("ck_ativos_tipo_valido"), "ativos", f"tipo IN ({_ANTES})")
