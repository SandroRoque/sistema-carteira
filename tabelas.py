"""Database schema (SQLAlchemy Core) — the source of truth for Alembic migrations.

Tenancy model
-------------
usuarios      : an account that logs in. It owns everything it uploads.
investidores  : one portfolio per CPF found on the account's notas (positions
                and taxes are per person). The CPF is a partition key only and
                is never stored: see contas.pseudonimizar_cpf.

Every portfolio fact (notas, negociacoes, b3_movimentacoes, ...) belongs to
exactly one investidor and must always be queried with an investidor_id filter.

Row-level security: the tenant-owned tables below have Postgres RLS policies
(created in migration 0003, not expressible here). Web requests run as the
restricted role carteira_app with app.usuario_id set, so a query that forgets
its investidor_id filter still only sees the logged-in account's rows. See
database.connect().

Data minimization (LGPD art. 6, III): identity data printed on the documents —
CPF, name, address, broker client code — is read during extraction and
discarded; only what the reports need is persisted.

ativos / ticker_aliases are a shared catalog: a listed instrument (PETR4) is
the same for every investidor, so it is stored once. So is the cotacoes
price cache.

Types
-----
Money and quantities are NUMERIC (exact). Dates are DATE. See database.py for
how NUMERIC values are currently loaded into Python.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
)

# Deterministic constraint names so Alembic migrations are reproducible.
metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def _criado_em() -> Column:
    return Column("criado_em", DateTime(timezone=True), nullable=False, server_default=func.now())


def _money(name: str, **kw) -> Column:
    return Column(name, Numeric, **kw)


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

usuarios = Table(
    "usuarios",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("email", Text, nullable=False),
    Column("nome", Text),
    # Argon2id hash. NULL = cannot log in (e.g. account imported by migra_sqlite
    # until a password is set with admin.py).
    Column("senha_hash", Text),
    # Administrators curate the shared ativos catalog.
    Column("e_admin", Boolean, nullable=False, server_default="false"),
    Column("falhas_login", Integer, nullable=False, server_default="0"),
    Column("bloqueado_ate", DateTime(timezone=True)),
    _criado_em(),
)
Index("uq_usuarios_email_lower", func.lower(usuarios.c.email), unique=True)

investidores = Table(
    "investidores",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("usuario_id", BigInteger, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
    # HMAC-SHA256 of the CPF with a server secret: recognizes the same CPF on
    # later uploads without storing it.
    Column("cpf_hash", Text, nullable=False),
    # e.g. ***.456.789-** — lets the user tell portfolios apart.
    Column("cpf_mascarado", Text, nullable=False),
    # User-chosen label ("Eu", "Esposa").
    Column("apelido", Text),
    _criado_em(),
    UniqueConstraint("usuario_id", "cpf_hash", name="uq_investidores_usuario_cpf_hash"),
)

# Login sessions. The cookie holds a random token; only its SHA-256 is stored,
# so a database leak does not hand out live sessions.
sessoes = Table(
    "sessoes",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("usuario_id", BigInteger, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True),
    # Portfolio currently selected in the UI.
    Column("investidor_id", BigInteger, ForeignKey("investidores.id", ondelete="SET NULL")),
    Column("csrf_token", Text, nullable=False),
    _criado_em(),
    Column("expira_em", DateTime(timezone=True), nullable=False),
)

# ---------------------------------------------------------------------------
# Shared catalog
# ---------------------------------------------------------------------------

ativos = Table(
    "ativos",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("tipo", Text, nullable=False, server_default="desconhecido"),
    Column("subtipo", Text),
    Column("ticker", Text, unique=True),
    Column("nome", Text),
    Column("cnpj_emissor", Text),
    Column("emissor", Text),
    Column("indexador", Text),
    Column("taxa_prefixada", Numeric),
    Column("percentual_do_indexador", Numeric),
    Column("emissao", Date),
    Column("vencimento", Date),
    Column("revisado", Boolean, nullable=False, server_default="false"),
    CheckConstraint(
        "tipo IN ('acao', 'fii', 'bdr', 'tesouro_direto', 'renda_fixa', "
        "'recibo_subscricao', 'direito_subscricao', 'desconhecido')",
        name="tipo_valido",
    ),
)

# Maps the raw text found in PDFs / B3 reports to a canonical ativo.
ticker_aliases = Table(
    "ticker_aliases",
    metadata,
    Column("raw_text", Text, primary_key=True),
    Column("ativo_id", BigInteger, ForeignKey("ativos.id"), nullable=False, index=True),
)

# Market price cache (see cotacoes.py), shared by every account. preco is NULL
# when the market data source does not know the ticker.
cotacoes = Table(
    "cotacoes",
    metadata,
    Column("ticker", Text, primary_key=True),
    Column("preco", Numeric),
    Column("atualizado_em", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# ---------------------------------------------------------------------------
# Brokerage notes
# ---------------------------------------------------------------------------

_NOTA_MONEY_COLUMNS = [
    "liquido_para",
    # resumo dos negócios (NotaCorretagem only)
    "debentures", "vendas_a_vista", "compras_a_vista",
    "opcoes_compras", "opcoes_vendas", "operacoes_a_termo",
    "valor_das_operacoes_com_titulos_publicos",
    "valor_das_operacoes", "valor_liquido_das_operacoes",
    # resumo financeiro (NotaCorretagem only)
    "taxa_de_liquidacao", "taxa_de_registro", "total_clearing_cblc",
    "taxa_de_termo_opcoes", "taxa_a_n_a", "emolumentos", "total_bolsa",
    "corretagem", "iss", "irrf_sobre_operacoes", "outras",
    "total_corretagem_despesas",
    "taxa_operacional", "execucao", "taxa_de_custodia", "impostos",
    "pis_cofins", "taxa_de_transferencia_de_ativos", "execucao_casa",
]

notas = Table(
    "notas",
    metadata,
    Column("investidor_id", BigInteger, ForeignKey("investidores.id", ondelete="CASCADE"), nullable=False),
    Column("corretora_id", Text, nullable=False),
    Column("doc_type", Text, nullable=False),
    Column("nota_id", Text, nullable=False),
    Column("data_pregao", Date, nullable=False),
    Column("data_de_liquidacao", Date),
    Column("folha", Text),
    Column("nota_de", Text),
    Column("local", Text),
    Column("emissor", Text),
    Column("cnpj_emissor", Text),
    Column("comando", Text),
    Column("mercado", Text),
    Column("status", Text),
    *[_money(c) for c in _NOTA_MONEY_COLUMNS],
    Column("filename", Text, nullable=False),
    Column("processado_em", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("investidor_id", "corretora_id", "doc_type", "nota_id"),
)

negociacoes = Table(
    "negociacoes",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("investidor_id", BigInteger, nullable=False),
    Column("corretora_id", Text, nullable=False),
    Column("doc_type", Text, nullable=False),
    Column("nota_id", Text, nullable=False),
    Column("linha_na_nota", Integer, nullable=False, server_default="0"),
    Column("ativo_id", BigInteger, ForeignKey("ativos.id"), nullable=False),
    Column("data", Date, nullable=False),
    Column("sentido", Text, nullable=False),
    Column("tipo", Text, nullable=False),
    Column("debito_credito", Text),
    Column("quantidade", Numeric),
    Column("preco_unitario", Numeric),
    _money("valor_bruto"),
    _money("taxas_proporcionais", nullable=False, server_default="0"),
    _money("valor_liquido"),
    Column("mercado", Text),
    Column("tipo_de_mercado", Text),
    Column("prazo", Text),
    Column("observacao", Text),
    Column("indexador", Text),
    Column("taxa_cupom_percentual", Numeric),
    Column("percentual_do_indexador", Numeric),
    Column("emissao", Date),
    Column("vencimento", Date),
    Column("custodia", Text),
    Column("tipo_emitente", Text),
    Column("conta_bancaria", Text),
    Column("rendimentos", Text),
    _money("imposto_de_renda_federal"),
    _money("iof"),
    Column("especificacao_observacao", Text),
    _money("tx_bvmf"),
    _money("tx_agente_custodia"),
    ForeignKeyConstraint(
        ["investidor_id", "corretora_id", "doc_type", "nota_id"],
        ["notas.investidor_id", "notas.corretora_id", "notas.doc_type", "notas.nota_id"],
        ondelete="CASCADE",
        name="fk_negociacoes_nota",
    ),
    UniqueConstraint(
        "investidor_id", "corretora_id", "doc_type", "nota_id", "linha_na_nota",
        name="uq_negociacoes_linha",
    ),
    CheckConstraint("sentido IN ('entrada', 'saida')", name="sentido_valido"),
    CheckConstraint("quantidade IS NULL OR quantidade >= 0", name="quantidade_nao_negativa"),
    Index("ix_negociacoes_investidor_ativo_data", "investidor_id", "ativo_id", "data"),
)

# ---------------------------------------------------------------------------
# B3 movimentações reports
# ---------------------------------------------------------------------------

b3_arquivos_processados = Table(
    "b3_arquivos_processados",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("investidor_id", BigInteger, ForeignKey("investidores.id", ondelete="CASCADE"), nullable=False),
    Column("arquivo", Text, nullable=False),
    Column("processado_em", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("investidor_id", "arquivo", name="uq_b3_arquivos_investidor_arquivo"),
)

# Raw rows from B3 movimentações reports. ativo_id is NULL when the product
# can't be resolved.
#
# B3 reports are final for any date they cover (they lag ~2 days and are never
# amended), so ingestion deduplicates by (investidor_id, data): a date already
# loaded for an investidor is skipped as a whole.
b3_movimentacoes = Table(
    "b3_movimentacoes",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("investidor_id", BigInteger, ForeignKey("investidores.id", ondelete="CASCADE"), nullable=False),
    Column("arquivo_id", BigInteger, ForeignKey("b3_arquivos_processados.id", ondelete="CASCADE"), nullable=False),
    Column("sentido", Text, nullable=False),
    Column("data", Date, nullable=False),
    Column("movimentacao", Text, nullable=False),
    Column("produto_raw", Text, nullable=False),
    Column("ativo_id", BigInteger, ForeignKey("ativos.id")),
    Column("instituicao", Text),
    Column("quantidade", Numeric),
    Column("preco_unitario", Numeric),
    _money("valor"),
    Index("ix_b3_movimentacoes_investidor_data", "investidor_id", "data"),
    Index("ix_b3_movimentacoes_investidor_ativo", "investidor_id", "ativo_id", "movimentacao"),
)

# Cost basis for bonus shares (Bonificação em Ativos). custo_por_cota is NULL
# until the user informs the acquisition cost declared by the company.
bonificacoes = Table(
    "bonificacoes",
    metadata,
    Column(
        "b3_movimentacao_id",
        BigInteger,
        ForeignKey("b3_movimentacoes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("custo_por_cota", Numeric),
    CheckConstraint("custo_por_cota IS NULL OR custo_por_cota >= 0", name="custo_nao_negativo"),
)
