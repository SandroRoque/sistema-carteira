"""uploads

Uploaded notas and B3 reports, which double as the import job queue
(importacao.py). The file bytes live in `conteudo` only until processed;
a check constraint makes keeping them afterwards impossible.

Tenant-owned: row-level security limits each account to its own uploads,
and a B3 report can only point at one of the account's own portfolios.
The web role may list and enqueue; processing runs on the owner connection
(background job).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USUARIO_ATUAL = "NULLIF(current_setting('app.usuario_id', true), '')::bigint"
_POLITICA = (
    f"usuario_id = {_USUARIO_ATUAL} AND (investidor_id IS NULL OR investidor_id IN "
    f"(SELECT id FROM investidores WHERE usuario_id = {_USUARIO_ATUAL}))"
)


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("investidor_id", sa.BigInteger(), nullable=True),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("nome_arquivo", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("conteudo", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.Text(), server_default="pendente", nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=True),
        sa.Column("tentativas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "conteudo IS NULL OR status IN ('pendente', 'processando')",
            name=op.f("ck_uploads_conteudo_so_ate_processar"),
        ),
        sa.CheckConstraint(
            "status IN ('pendente', 'processando', 'concluido', 'duplicado', 'erro')",
            name=op.f("ck_uploads_status_valido"),
        ),
        sa.CheckConstraint(
            "tipo = 'nota' OR investidor_id IS NOT NULL", name=op.f("ck_uploads_b3_tem_carteira")
        ),
        sa.CheckConstraint("tipo IN ('nota', 'b3')", name=op.f("ck_uploads_tipo_valido")),
        sa.ForeignKeyConstraint(
            ["investidor_id"], ["investidores.id"],
            name=op.f("fk_uploads_investidor_id_investidores"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"],
            name=op.f("fk_uploads_usuario_id_usuarios"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_uploads")),
    )
    op.create_index(
        "ix_uploads_fila", "uploads", ["id"], unique=False,
        postgresql_where="status IN ('pendente', 'processando')",
    )
    op.create_index("ix_uploads_usuario_sha256", "uploads", ["usuario_id", "sha256"], unique=False)

    op.execute("GRANT SELECT, INSERT ON uploads TO carteira_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE uploads_id_seq TO carteira_app")
    op.execute("ALTER TABLE uploads ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY uploads_por_usuario ON uploads USING ({_POLITICA}) WITH CHECK ({_POLITICA})")


def downgrade() -> None:
    op.drop_table("uploads")
