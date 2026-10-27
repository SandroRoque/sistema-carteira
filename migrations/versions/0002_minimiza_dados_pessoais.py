"""minimiza dados pessoais

Replaces the stored CPF with a keyed HMAC plus a masked form, and drops the
identity data copied from the notas (CPF, name, address, broker client code,
advisor). Reports never used them; keeping them only added risk (LGPD art. 6, III).

Irreversible on purpose: the discarded data cannot be brought back.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""
import hashlib
import hmac
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUNAS_DESCARTADAS_NOTAS = (
    "cpf_cliente", "codigo_cliente", "nome_cliente", "assessor",
    "endereco", "cidade", "uf", "cep",
)


def upgrade() -> None:
    op.add_column("investidores", sa.Column("cpf_hash", sa.Text(), nullable=True))
    op.add_column("investidores", sa.Column("cpf_mascarado", sa.Text(), nullable=True))
    op.add_column("investidores", sa.Column("apelido", sa.Text(), nullable=True))

    conn = op.get_bind()
    investidores = conn.execute(sa.text("SELECT id, cpf FROM investidores")).all()
    if investidores:
        # Only needed when there is data to convert.
        from settings import cpf_hmac_key

        chave = cpf_hmac_key()
        for investidor_id, cpf in investidores:
            conn.execute(
                sa.text(
                    "UPDATE investidores SET cpf_hash = :h, cpf_mascarado = :m WHERE id = :id"
                ),
                {
                    "h": hmac.new(chave, cpf.encode(), hashlib.sha256).hexdigest(),
                    "m": f"***.{cpf[3:6]}.{cpf[6:9]}-**",
                    "id": investidor_id,
                },
            )

    op.alter_column("investidores", "cpf_hash", nullable=False)
    op.alter_column("investidores", "cpf_mascarado", nullable=False)
    op.drop_constraint(op.f("uq_investidores_usuario_cpf"), "investidores", type_="unique")
    op.create_unique_constraint(
        op.f("uq_investidores_usuario_cpf_hash"), "investidores", ["usuario_id", "cpf_hash"]
    )
    op.drop_constraint(op.f("ck_investidores_cpf_digitos"), "investidores", type_="check")
    op.drop_column("investidores", "cpf")
    # The name came from the notas; users set their own apelido instead.
    op.drop_column("investidores", "nome")

    for coluna in _COLUNAS_DESCARTADAS_NOTAS:
        op.drop_column("notas", coluna)


def downgrade() -> None:
    raise NotImplementedError(
        "0002 é irreversível: os dados pessoais foram descartados de propósito. "
        "Para recriar o banco local do zero: docker compose down -v."
    )
