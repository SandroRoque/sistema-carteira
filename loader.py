import re
import sqlite3
from datetime import date

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


def ja_processado(conn: sqlite3.Connection, nota_id: str, corretora_id: str, doc_type: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM notas WHERE nota_id = ? AND corretora_id = ? AND doc_type = ?",
        (nota_id, corretora_id, doc_type),
    ).fetchone() is not None


def resolve_ou_criar_ativo(
    conn: sqlite3.Connection,
    raw_ticker: str,
    doc_type: str = "",
    *,
    cnpj_emissor: str | None = None,
    emissor: str | None = None,
    indexador: str | None = None,
    taxa_prefixada: float | None = None,
    percentual_do_indexador: float | None = None,
    emissao: str | None = None,
    vencimento: str | None = None,
) -> int:
    """Returns the ativo_id for raw_ticker, auto-creating an unreviewed ativo on first encounter.

    Canonical ticker extraction ("PETR4F PN N2" → "PETR4") prevents the same
    asset from being stored as multiple ativos due to ex-date or lot-size
    qualifiers in the raw description.  All raw variants are recorded in
    ticker_aliases pointing to the single canonical ativo.

    Extra keyword arguments enrich the new ativo row for fixed income instruments.
    They are only used during creation; callers that don't provide them (e.g.
    carrega_b3.py) get the same behaviour as before.
    """
    # 1. Fast path: alias already exists.
    row = conn.execute(
        "SELECT ativo_id FROM ticker_aliases WHERE raw_text = ?", (raw_ticker,)
    ).fetchone()
    if row:
        return row["ativo_id"]

    # 2. Extract canonical ticker (strips fractional-lot 'F' suffix and qualifiers).
    ticker = _extrair_ticker(raw_ticker)
    tipo = _infer_tipo(ticker, doc_type)

    # 3. If we have a canonical ticker, reuse an existing ativo with that ticker
    #    to avoid fragmentation (e.g. "PETR4F PN N2" and "PETR4F PN EDJ N2"
    #    both resolve to the same PETR4 ativo).
    if ticker:
        existing = conn.execute(
            "SELECT id FROM ativos WHERE ticker = ?", (ticker,)
        ).fetchone()
        if existing:
            ativo_id = existing["id"]
            conn.execute(
                "INSERT OR IGNORE INTO ticker_aliases (raw_text, ativo_id) VALUES (?, ?)",
                (raw_ticker, ativo_id),
            )
            return ativo_id

    # 4. New ativo.
    cursor = conn.execute(
        """
        INSERT INTO ativos
            (tipo, ticker, nome, cnpj_emissor, emissor, indexador,
             taxa_prefixada, percentual_do_indexador, emissao, vencimento, revisado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (tipo, ticker, raw_ticker, cnpj_emissor, emissor, indexador,
         taxa_prefixada, percentual_do_indexador, emissao, vencimento),
    )
    ativo_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO ticker_aliases (raw_text, ativo_id) VALUES (?, ?)",
        (raw_ticker, ativo_id),
    )
    return ativo_id


def _iso(value) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    return value


def _inserir_nota(conn: sqlite3.Connection, nota: NotaRecord, filename: str) -> None:
    conn.execute(
        """
        INSERT INTO notas (
            nota_id, corretora_id, doc_type,
            data_pregao, data_de_liquidacao,
            cpf_cliente, codigo_cliente, nome_cliente,
            assessor, folha, endereco, cidade, uf, cep,
            nota_de, local, emissor, cnpj_emissor, comando,
            mercado, status, liquido_para,
            debentures, vendas_a_vista, compras_a_vista,
            opcoes_compras, opcoes_vendas, operacoes_a_termo,
            valor_das_operacoes_com_titulos_publicos,
            valor_das_operacoes, valor_liquido_das_operacoes,
            taxa_de_liquidacao, taxa_de_registro, total_clearing_cblc,
            taxa_de_termo_opcoes, taxa_a_n_a, emolumentos, total_bolsa,
            corretagem, iss, irrf_sobre_operacoes, outras,
            total_corretagem_despesas,
            taxa_operacional, execucao, taxa_de_custodia, impostos,
            pis_cofins, taxa_de_transferencia_de_ativos, execucao_casa,
            filename
        ) VALUES (
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?
        )
        """,
        (
            nota.nota_id, nota.corretora_id, nota.doc_type,
            _iso(nota.data_pregao), _iso(nota.data_de_liquidacao),
            nota.cpf_cliente, nota.codigo_cliente, nota.nome_cliente,
            nota.assessor, nota.folha, nota.endereco, nota.cidade, nota.uf, nota.cep,
            nota.nota_de, nota.local, nota.emissor, nota.cnpj_emissor, nota.comando,
            nota.mercado, nota.status, nota.liquido_para,
            nota.debentures, nota.vendas_a_vista, nota.compras_a_vista,
            nota.opcoes_compras, nota.opcoes_vendas, nota.operacoes_a_termo,
            nota.valor_das_operacoes_com_titulos_publicos,
            nota.valor_das_operacoes, nota.valor_liquido_das_operacoes,
            nota.taxa_de_liquidacao, nota.taxa_de_registro, nota.total_clearing_cblc,
            nota.taxa_de_termo_opcoes, nota.taxa_a_n_a, nota.emolumentos, nota.total_bolsa,
            nota.corretagem, nota.iss, nota.irrf_sobre_operacoes, nota.outras,
            nota.total_corretagem_despesas,
            nota.taxa_operacional, nota.execucao, nota.taxa_de_custodia, nota.impostos,
            nota.pis_cofins, nota.taxa_de_transferencia_de_ativos, nota.execucao_casa,
            filename,
        ),
    )


def _inserir_negociacao(conn: sqlite3.Connection, neg: NegociacaoRecord, ativo_id: int) -> None:
    conn.execute(
        """
        INSERT INTO negociacoes (
            nota_id, corretora_id, doc_type, linha_na_nota, ativo_id,
            data, sentido, tipo, debito_credito,
            quantidade, preco_unitario, valor_bruto,
            taxas_proporcionais, valor_liquido,
            mercado, tipo_de_mercado, prazo, observacao,
            indexador, taxa_cupom_percentual, percentual_do_indexador,
            emissao, vencimento,
            custodia, tipo_emitente, conta_bancaria,
            rendimentos, imposto_de_renda_federal, iof,
            especificacao_observacao, tx_bvmf, tx_agente_custodia
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?
        )
        """,
        (
            neg.nota_id, neg.corretora_id, neg.doc_type, neg.linha_na_nota, ativo_id,
            _iso(neg.data), neg.sentido, neg.tipo, neg.debito_credito,
            neg.quantidade, neg.preco_unitario, neg.valor_bruto,
            neg.taxas_proporcionais, neg.valor_liquido,
            neg.mercado, neg.tipo_de_mercado, neg.prazo, neg.observacao,
            neg.indexador, neg.taxa_cupom_percentual, neg.percentual_do_indexador,
            _iso(neg.emissao), _iso(neg.vencimento),
            neg.custodia, neg.tipo_emitente, neg.conta_bancaria,
            neg.rendimentos, neg.imposto_de_renda_federal, neg.iof,
            neg.especificacao_observacao, neg.tx_bvmf, neg.tx_agente_custodia,
        ),
    )


def carregar(conn: sqlite3.Connection, doc: DocumentoTransformado, filename: str) -> bool:
    """Inserts a DocumentoTransformado into the database.

    Returns True if the nota was inserted, False if it was already present
    (idempotent — safe to call multiple times for the same nota).
    """
    nota = doc.nota
    if ja_processado(conn, nota.nota_id, nota.corretora_id, nota.doc_type):
        return False

    _inserir_nota(conn, nota, filename)

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
            emissao=_iso(neg.emissao),
            vencimento=_iso(neg.vencimento),
        )
        _inserir_negociacao(conn, neg, ativo_id)

    return True
