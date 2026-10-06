"""autenticacao e rls

Login (password hash, lockout, sessions) and row-level security.

Web requests run as the restricted role carteira_app, with the logged-in
account in the transaction-local setting app.usuario_id (database.connect).
RLS policies on every tenant-owned table only expose rows of that account,
so a query that forgets its investidor_id filter returns nothing instead of
another account's data.

The owner role (migrations, CLI tools, auth) is not subject to the policies:
RLS is ENABLEd, not FORCEd. carteira_app gets no access to usuarios or
sessoes at all — authentication runs on the owner connection.

A new tenant-owned table needs its own GRANT and policy in its migration.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USUARIO_ATUAL = "NULLIF(current_setting('app.usuario_id', true), '')::bigint"
_INVESTIDORES_DO_USUARIO = f"(SELECT id FROM investidores WHERE usuario_id = {_USUARIO_ATUAL})"

_POLITICAS = {
    "investidores": f"usuario_id = {_USUARIO_ATUAL}",
    "notas": f"investidor_id IN {_INVESTIDORES_DO_USUARIO}",
    "negociacoes": f"investidor_id IN {_INVESTIDORES_DO_USUARIO}",
    "b3_arquivos_processados": f"investidor_id IN {_INVESTIDORES_DO_USUARIO}",
    "b3_movimentacoes": f"investidor_id IN {_INVESTIDORES_DO_USUARIO}",
    # Inherits the owner through b3_movimentacoes, whose own policy applies here.
    "bonificacoes": "b3_movimentacao_id IN (SELECT id FROM b3_movimentacoes)",
}

# Shared catalog: readable and extendable by every account. Changing existing
# rows is gated to administrators in the application.
_CATALOGO = ("ativos", "ticker_aliases")


def upgrade() -> None:
    op.create_table(
        "sessoes",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("investidor_id", sa.BigInteger(), nullable=True),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["investidor_id"], ["investidores.id"],
            name=op.f("fk_sessoes_investidor_id_investidores"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"],
            name=op.f("fk_sessoes_usuario_id_usuarios"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_sessoes")),
    )
    op.create_index(op.f("ix_sessoes_usuario_id"), "sessoes", ["usuario_id"], unique=False)
    op.add_column("usuarios", sa.Column("senha_hash", sa.Text(), nullable=True))
    op.add_column("usuarios", sa.Column("e_admin", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("usuarios", sa.Column("falhas_login", sa.Integer(), server_default="0", nullable=False))
    op.add_column("usuarios", sa.Column("bloqueado_ate", sa.DateTime(timezone=True), nullable=True))

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'carteira_app') THEN
                CREATE ROLE carteira_app NOLOGIN;
            END IF;
        END
        $$
    """)
    # The connecting role switches into carteira_app with SET LOCAL ROLE.
    op.execute("GRANT carteira_app TO CURRENT_USER")
    op.execute("GRANT USAGE ON SCHEMA public TO carteira_app")

    for tabela, condicao in _POLITICAS.items():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tabela} TO carteira_app")
        op.execute(f"ALTER TABLE {tabela} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {tabela}_por_usuario ON {tabela} "
            f"USING ({condicao}) WITH CHECK ({condicao})"
        )
    for tabela in _CATALOGO:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {tabela} TO carteira_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO carteira_app")


def downgrade() -> None:
    for tabela in _POLITICAS:
        op.execute(f"DROP POLICY IF EXISTS {tabela}_por_usuario ON {tabela}")
        op.execute(f"ALTER TABLE {tabela} DISABLE ROW LEVEL SECURITY")
    # The role is cluster-wide (other databases may use it): revoke, don't drop.
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM carteira_app")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM carteira_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM carteira_app")

    op.drop_column("usuarios", "bloqueado_ate")
    op.drop_column("usuarios", "falhas_login")
    op.drop_column("usuarios", "e_admin")
    op.drop_column("usuarios", "senha_hash")
    op.drop_index(op.f("ix_sessoes_usuario_id"), table_name="sessoes")
    op.drop_table("sessoes")
