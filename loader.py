import dataclasses
import re
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection

import classes_b3
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


def _classificar(ticker: str | None, doc_type: str) -> tuple[str, str | None]:
    """(tipo, subtipo) of a new ativo, from B3's class table or the ticker pattern."""
    if doc_type == "TituloPublico":
        return "tesouro_direto", None
    if doc_type == "TituloPrivado":
        return "renda_fixa", None
    if ticker and (classe := classes_b3.classe(ticker)):
        return classe
    return _infer_tipo(ticker), None


def _infer_tipo(ticker: str | None) -> str:
    """Asset type from the ticker suffix, for tickers B3's table does not know."""
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
    taxa_prefixada: Decimal | None = None,
    percentual_do_indexador: Decimal | None = None,
    emissao: date | None = None,
    vencimento: date | None = None,
    data: date | None = None,
    exercicio: bool = False,
    opcao: bool = False,
) -> int:
    """Returns the ativo_id for raw_ticker, auto-creating an unreviewed ativo on first encounter.

    Market-standard notas describe the security by name and specification
    ("PETROBRAS PN N2"): that is resolved to the ticker traded under it on
    `data` (especificacoes_b3). An option exercise (`exercicio`) is a trade
    of the underlying shares, so it resolves to them. An option (`opcao`)
    gets no ticker: B3 reuses option codes, so raw_ticker (see
    _nome_da_opcao) carries the expiry and is what tells options apart.

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
    if opcao:
        ativo_id = scalar(
            conn,
            "INSERT INTO ativos (tipo, nome, revisado) VALUES ('opcao', :nome, false) RETURNING id",
            nome=raw_ticker,
        )
        return _registrar_alias(conn, raw_ticker, ativo_id)
    if doc_type == "TituloPrivado" and (
        existente := _renda_fixa_equivalente(
            conn, cnpj_emissor, indexador, taxa_prefixada, percentual_do_indexador, emissao, vencimento
        )
    ):
        # Same bond under a differently written title ("CDB PREFIXADO 14,30% AA"
        # vs "CDB 14.30% PREFIXADO AA"): its terms identify it.
        return _registrar_alias(conn, raw_ticker, existente)
    if exercicio:
        ticker = especificacoes_b3.subjacente(raw_ticker, data)
    else:
        ticker = _extrair_ticker(raw_ticker) or especificacoes_b3.resolver(raw_ticker, data)
    tipo, subtipo = _classificar(ticker, doc_type)

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
            (tipo, subtipo, ticker, nome, cnpj_emissor, emissor, indexador,
             taxa_prefixada, percentual_do_indexador, emissao, vencimento, revisado)
        VALUES
            (:tipo, :subtipo, :ticker, :nome, :cnpj_emissor, :emissor, :indexador,
             :taxa_prefixada, :percentual_do_indexador, :emissao, :vencimento, false)
        ON CONFLICT (ticker) DO NOTHING
        RETURNING id
        """,
        tipo=tipo,
        subtipo=subtipo,
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


# Terms that identify a private fixed-income bond, whatever its title text.
_MESMOS_TERMOS = """
    tipo = 'renda_fixa'
    AND regexp_replace(cnpj_emissor, '[^0-9]', '', 'g') = regexp_replace(:cnpj, '[^0-9]', '', 'g')
    AND vencimento = :vencimento
    AND upper(trim(indexador)) IS NOT DISTINCT FROM upper(trim(:indexador))
    AND taxa_prefixada IS NOT DISTINCT FROM CAST(:taxa AS numeric)
    AND percentual_do_indexador IS NOT DISTINCT FROM CAST(:percentual AS numeric)
    AND emissao IS NOT DISTINCT FROM CAST(:emissao AS date)
"""


def _renda_fixa_equivalente(
    conn: Connection, cnpj_emissor, indexador, taxa_prefixada, percentual_do_indexador, emissao, vencimento
) -> int | None:
    """The ativo of a bond with the same issuer and terms, if one exists."""
    if not cnpj_emissor or vencimento is None:
        return None
    return scalar(
        conn,
        f"SELECT id FROM ativos WHERE {_MESMOS_TERMOS} ORDER BY revisado DESC, id LIMIT 1",
        cnpj=cnpj_emissor, vencimento=vencimento, indexador=indexador,
        taxa=taxa_prefixada, percentual=percentual_do_indexador, emissao=emissao,
    )


def mesclar_renda_fixa_duplicada(conn: Connection) -> list[tuple[int, int]]:
    """Merge unreviewed fixed-income ativos that repeat another one's terms.

    For bonds loaded before titles were matched by their terms. Each
    duplicate's aliases and trades move to the ativo kept (a reviewed one if
    any, else the oldest); a duplicate an investor already attached a cost or
    a conversion to is left alone. ativos is shared, so this runs over every
    account: for admin.py. Returns (duplicate, kept) pairs.
    """
    mesclados = []
    for a in fetch_all(
        conn,
        """
        SELECT id, cnpj_emissor, indexador, taxa_prefixada, percentual_do_indexador, emissao, vencimento
        FROM ativos
        WHERE tipo = 'renda_fixa' AND NOT revisado AND cnpj_emissor IS NOT NULL AND vencimento IS NOT NULL
        ORDER BY id DESC
        """,
    ):
        manter = _renda_fixa_equivalente(
            conn, a["cnpj_emissor"], a["indexador"], a["taxa_prefixada"],
            a["percentual_do_indexador"], a["emissao"], a["vencimento"],
        )
        if manter is None or manter == a["id"]:
            continue
        if scalar(
            conn,
            """
            SELECT EXISTS (SELECT 1 FROM custos_informados WHERE ativo_id = :id)
                OR EXISTS (SELECT 1 FROM conversoes WHERE ativo_origem_id = :id)
            """,
            id=a["id"],
        ):
            continue
        for tabela in ("ticker_aliases", "negociacoes", "b3_movimentacoes"):
            execute(conn, f"UPDATE {tabela} SET ativo_id = :manter WHERE ativo_id = :id", manter=manter, id=a["id"])
        execute(conn, "DELETE FROM ativos WHERE id = :id", id=a["id"])
        mesclados.append((a["id"], manter))
    return mesclados


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


def _nome_da_opcao(neg: NegociacaoRecord) -> str:
    """'ABCDK350 PN 35,00 ABCD · opção de compra · venc. 11/25': the code as
    printed (some notas omit it), the kind and the expiry."""
    especie = "compra" if "COMPRA" in (neg.tipo_de_mercado or "").upper() else "venda"
    return f"{neg.raw_ticker} · opção de {especie} · venc. {neg.prazo or '?'}"


# Ids of the market-standard layout: printed number, date, trade fingerprint
# (extractors.sinacor._identidade). Nu's own layout uses the printed number.
_ID_SINACOR = re.compile(r"^.+-\d{8}-[0-9a-f]{8}$")


def _negocios(itens) -> list[tuple]:
    centavo = Decimal("0.01")
    return sorted(
        (n["sentido"], Decimal(str(n["quantidade"] or 0)).normalize(),
         Decimal(str(n["valor_bruto"] or 0)).quantize(centavo))
        for n in itens
    )


def _mesma_nota_em_outro_modelo(conn: Connection, investidor_id: int, doc: DocumentoTransformado) -> bool:
    """The nota is already loaded from the broker's other layout.

    Nu offers each nota in its own layout and in the market-standard one,
    and the two get ids of different forms. A nota of the other form with
    the same broker, day, final amount and trades is the same nota."""
    nota = doc.nota
    if nota.doc_type != "NotaCorretagem" or nota.liquido_para is None:
        return False
    sinacor = bool(_ID_SINACOR.match(nota.nota_id))
    candidatas = fetch_all(
        conn,
        """
        SELECT nota_id FROM notas
        WHERE investidor_id = :i AND corretora_id = :c AND doc_type = :d
          AND data_pregao = :dia AND liquido_para = :liquido
        """,
        i=investidor_id, c=nota.corretora_id, d=nota.doc_type, dia=nota.data_pregao, liquido=nota.liquido_para,
    )
    novos = _negocios(dataclasses.asdict(n) for n in doc.negociacoes)
    for r in candidatas:
        if bool(_ID_SINACOR.match(r["nota_id"])) == sinacor:
            continue  # same layout, different nota: two notas can match by chance
        existentes = fetch_all(
            conn,
            "SELECT sentido, quantidade, valor_bruto FROM negociacoes "
            "WHERE investidor_id = :i AND corretora_id = :c AND nota_id = :n",
            i=investidor_id, c=nota.corretora_id, n=r["nota_id"],
        )
        if _negocios(existentes) == novos:
            return True
    return False


def _completar_irrf_day_trade(conn: Connection, investidor_id: int, nota: NotaRecord) -> None:
    """A nota loaded before the day-trade IRRF was read gets it when sent
    again; nothing else of a loaded nota changes."""
    if nota.irrf_day_trade is None:
        return
    execute(
        conn,
        """
        UPDATE notas SET irrf_day_trade = :v
        WHERE investidor_id = :i AND nota_id = :n AND corretora_id = :c AND doc_type = :d
          AND irrf_day_trade IS NULL
        """,
        v=nota.irrf_day_trade, i=investidor_id, n=nota.nota_id, c=nota.corretora_id, d=nota.doc_type,
    )


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
        _completar_irrf_day_trade(conn, investidor_id, nota)
        return False
    if _mesma_nota_em_outro_modelo(conn, investidor_id, doc):
        return False

    _inserir_nota(conn, investidor_id, nota, filename)

    for neg in doc.negociacoes:
        mercado = (neg.tipo_de_mercado or "").upper()
        # Option trades are kept under their own ativos; positions and taxes
        # leave them out for now (the Impostos and Posições pages say so).
        opcao = mercado.startswith("OPCAO")
        ativo_id = resolve_ou_criar_ativo(
            conn,
            _nome_da_opcao(neg) if opcao else neg.raw_ticker,
            neg.doc_type,
            cnpj_emissor=nota.cnpj_emissor,
            emissor=nota.emissor,
            indexador=neg.indexador,
            taxa_prefixada=neg.taxa_cupom_percentual,
            percentual_do_indexador=neg.percentual_do_indexador,
            emissao=neg.emissao,
            vencimento=neg.vencimento,
            data=neg.data,
            exercicio=mercado.startswith("EXERC"),
            opcao=opcao,
        )
        _inserir_negociacao(conn, investidor_id, neg, ativo_id)

    return True
