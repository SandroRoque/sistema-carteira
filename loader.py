import dataclasses
import re
from datetime import date

from sqlalchemy import Connection

import especificacoes_b3
import tabelas
from contas import get_or_create_investidor
from database import execute, fetch_all, fetch_one, scalar
from transformer import DocumentoTransformado, NegociacaoRecord, NotaRecord

# Matches canonical B3 tickers: a letter followed by 2-3 alphanumeric chars
# (to handle tickers like B3SA3 where the company name embeds a digit) then
# 1-2 digit suffix, plus an optional trailing 'F' (fractional-lot market).
# Examples: "PETR4F" → "PETR4", "BTCI11" → "BTCI11", "B3SA3F" → "B3SA3".
_B3_TICKER_RE = re.compile(r'^([A-Z][A-Z0-9]{2,3}\d{1,2})F?$')


def _extrair_ticker(raw: str) -> str | None:
    """Extract the canonical B3 ticker from a raw asset description.

    Returns None for fixed income, Tesouro Direto, and anything that
    doesn't look like an equity/FII/BDR ticker.
    """
    first = raw.strip().split()[0].upper()
    m = _B3_TICKER_RE.match(first)
    return m.group(1) if m else None


def _infer_tipo(ticker: str | None, doc_type: str) -> str:
    """Infer asset type from ticker pattern and document type."""
    if doc_type == "TituloPublico":
        return "tesouro_direto"
    if doc_type == "TituloPrivado":
        return "renda_fixa"
    if ticker:
        if ticker.endswith("11"):
            return "fii"
        # FII subscription receipts — same fund, second series of cotas
        if ticker.endswith("12"):
            return "recibo_subscricao"
        # BDR suffixes on B3: 31–39 (e.g. 32=NASDAQ, 34=NYSE)
        if re.search(r'3[1-9]$', ticker):
            return "bdr"
        # Subscription rights: tickers ending in 1, 2, or 9
        # (e.g. ITSA1/2 = direitos, ITSA9 = sobras de subscrição)
        if re.search(r'[129]$', ticker):
            return "direito_subscricao"
        return "acao"
    return "desconhecido"


def ja_processado(
    conn: Connection, investidor_id: int, nota_id: str, corretora_id: str, doc_type: str
) -> bool:
    return fetch_one(
        conn,
        """
        SELECT 1 FROM notas
        WHERE investidor_id = :investidor_id AND nota_id = :nota_id
          AND corretora_id = :corretora_id AND doc_type = :doc_type
        """,
        investidor_id=investidor_id,
        nota_id=nota_id,
        corretora_id=corretora_id,
        doc_type=doc_type,
    ) is not None


def _registrar_alias(conn: Connection, raw_ticker: str, ativo_id: int) -> int:
    """Point raw_ticker at ativo_id unless another loader got there first.

    Returns the ativo_id the alias resolves to after the insert attempt.
    """
    execute(
        conn,
        """
        INSERT INTO ticker_aliases (raw_text, ativo_id) VALUES (:raw, :ativo_id)
        ON CONFLICT (raw_text) DO NOTHING
        """,
        raw=raw_ticker,
        ativo_id=ativo_id,
    )
    # Separate statement: under READ COMMITTED it sees a row committed by a
    # concurrent loader that won the conflict.
    return scalar(conn, "SELECT ativo_id FROM ticker_aliases WHERE raw_text = :raw", raw=raw_ticker)


def resolve_ou_criar_ativo(
    conn: Connection,
    raw_ticker: str,
    doc_type: str = "",
    *,
    cnpj_emissor: str | None = None,
    emissor: str | None = None,
    indexador: str | None = None,
    taxa_prefixada: float | None = None,
    percentual_do_indexador: float | None = None,
    emissao: date | None = None,
    vencimento: date | None = None,
    data: date | None = None,
    exercicio: bool = False,
) -> int:
    """Returns the ativo_id for raw_ticker, auto-creating an unreviewed ativo on first encounter.

    Market-standard notas describe the security by name and specification
    ("PETROBRAS PN N2"): that is resolved to the ticker traded under it on
    `data` (especificacoes_b3). An option exercise (`exercicio`) is a trade
    of the underlying shares, so it resolves to them.

    Canonical ticker extraction ("PETR4F PN N2" → "PETR4") prevents the same
    asset from being stored as multiple ativos due to ex-date or lot-size
    qualifiers in the raw description.  All raw variants are recorded in
    ticker_aliases pointing to the single canonical ativo.

    Extra keyword arguments enrich the new ativo row for fixed income instruments.
    They are only used during creation; callers that don't provide them (e.g.
    carrega_b3.py) get the same behaviour as before.

    ativos is a catalog shared by all investidores, so creation is written to be
    safe when two imports race on the same ticker.
    """
    # 1. Fast path: alias already exists.
    row = fetch_one(conn, "SELECT ativo_id FROM ticker_aliases WHERE raw_text = :raw", raw=raw_ticker)
    if row:
        return row["ativo_id"]

    # 2. Extract canonical ticker (strips fractional-lot 'F' suffix and qualifiers).
    if exercicio:
        ticker = especificacoes_b3.subjacente(raw_ticker, data)
    else:
        ticker = _extrair_ticker(raw_ticker) or especificacoes_b3.resolver(raw_ticker, data)
    tipo = _infer_tipo(ticker, doc_type)

    # 3. If we have a canonical ticker, reuse an existing ativo with that ticker
    #    to avoid fragmentation (e.g. "PETR4F PN N2" and "PETR4F PN EDJ N2"
    #    both resolve to the same PETR4 ativo).
    if ticker:
        existing = fetch_one(conn, "SELECT id FROM ativos WHERE ticker = :ticker", ticker=ticker)
        if existing:
            return _registrar_alias(conn, raw_ticker, existing["id"])

    # 4. New ativo. ON CONFLICT covers a concurrent insert of the same ticker.
    ativo_id = scalar(
        conn,
        """
        INSERT INTO ativos
            (tipo, ticker, nome, cnpj_emissor, emissor, indexador,
             taxa_prefixada, percentual_do_indexador, emissao, vencimento, revisado)
        VALUES
            (:tipo, :ticker, :nome, :cnpj_emissor, :emissor, :indexador,
             :taxa_prefixada, :percentual_do_indexador, :emissao, :vencimento, false)
        ON CONFLICT (ticker) DO NOTHING
        RETURNING id
        """,
        tipo=tipo,
        ticker=ticker,
        nome=raw_ticker,
        cnpj_emissor=cnpj_emissor,
        emissor=emissor,
        indexador=indexador,
        taxa_prefixada=taxa_prefixada,
        percentual_do_indexador=percentual_do_indexador,
        emissao=emissao,
        vencimento=vencimento,
    )
    if ativo_id is None:
        ativo_id = scalar(conn, "SELECT id FROM ativos WHERE ticker = :ticker", ticker=ticker)
    return _registrar_alias(conn, raw_ticker, ativo_id)


def _colunas(tabela, registro, **extra) -> dict:
    """Dataclass fields that are columns of `tabela`, plus explicit extras."""
    nomes = set(tabela.c.keys())
    valores = {k: v for k, v in dataclasses.asdict(registro).items() if k in nomes}
    valores.update(extra)
    return valores


def _inserir_nota(conn: Connection, investidor_id: int, nota: NotaRecord, filename: str) -> None:
    conn.execute(
        tabelas.notas.insert().values(
            _colunas(tabelas.notas, nota, investidor_id=investidor_id, filename=filename)
        )
    )


def _inserir_negociacao(
    conn: Connection, investidor_id: int, neg: NegociacaoRecord, ativo_id: int
) -> None:
    conn.execute(
        tabelas.negociacoes.insert().values(
            _colunas(tabelas.negociacoes, neg, investidor_id=investidor_id, ativo_id=ativo_id)
        )
    )


class NotaSemCpf(ValueError):
    """A nota without a CPF, and no way to tell which portfolio it belongs to."""


def investidor_da_nota(conn: Connection, usuario_id: int, nota: NotaRecord, carteira: int | None = None) -> int:
    """The portfolio of a nota: the one of the CPF printed on it. A nota
    without a CPF goes to `carteira` (picked at upload) or, failing that, to
    the account's only portfolio."""
    if nota.cpf_cliente:
        return get_or_create_investidor(conn, usuario_id, nota.cpf_cliente)
    if carteira is not None and fetch_one(
        conn, "SELECT 1 FROM investidores WHERE id = :id AND usuario_id = :u", id=carteira, u=usuario_id
    ):
        return carteira
    ids = [r["id"] for r in fetch_all(conn, "SELECT id FROM investidores WHERE usuario_id = :u", u=usuario_id)]
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise NotaSemCpf("A nota não traz o CPF. Envie antes uma nota com CPF para criar a carteira.")
    raise NotaSemCpf("A nota não traz o CPF. Escolha a carteira dela no envio e mande de novo.")


def carregar(
    conn: Connection, usuario_id: int, doc: DocumentoTransformado, filename: str, carteira: int | None = None
) -> bool:
    """Inserts a DocumentoTransformado into the database.

    The nota is filed under the investidor for the CPF printed on it,
    creating that investidor for usuario_id on first sight. Identity fields of
    the nota (CPF, name, address...) are not persisted: tabelas.notas has no
    columns for them, so _colunas drops them.

    Returns True if the nota was inserted, False if it was already present
    (idempotent — safe to call multiple times for the same nota).
    """
    nota = doc.nota
    investidor_id = investidor_da_nota(conn, usuario_id, nota, carteira)
    if ja_processado(conn, investidor_id, nota.nota_id, nota.corretora_id, nota.doc_type):
        return False

    _inserir_nota(conn, investidor_id, nota, filename)

    for neg in doc.negociacoes:
        ativo_id = resolve_ou_criar_ativo(
            conn,
            neg.raw_ticker,
            neg.doc_type,
            cnpj_emissor=nota.cnpj_emissor,
            emissor=nota.emissor,
            indexador=neg.indexador,
            taxa_prefixada=neg.taxa_cupom_percentual,
            percentual_do_indexador=neg.percentual_do_indexador,
            emissao=neg.emissao,
            vencimento=neg.vencimento,
            data=neg.data,
            exercicio=(neg.tipo_de_mercado or "").upper().startswith("EXERC"),
        )
        _inserir_negociacao(conn, investidor_id, neg, ativo_id)

    return True
