"""FastAPI web app for portfolio CRUD.

Run with:
    uvicorn app.main:app --reload

Routes are plain `def` (not `async def`): they do blocking database I/O,
so FastAPI runs them in its threadpool instead of on the event loop.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Annotated

# Make project root importable when running from the project root directory.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from contas import investidor_do_cli
from database import connect, execute, fetch_all, fetch_one, scalar
from posicoes import calcular_posicoes

_HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# Jinja2 filters
# ---------------------------------------------------------------------------

def _brl(v: float | None) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}"


def _qty(v: float | None) -> str:
    if v is None:
        return "—"
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s


_TIPO_LABEL = {
    "acao": "Ações",
    "fii": "FIIs",
    "bdr": "BDRs",
    "tesouro_direto": "Tesouro Direto",
    "renda_fixa": "Renda Fixa",
    "recibo_subscricao": "Recibos de Subscrição",
    "direito_subscricao": "Direitos de Subscrição",
    "desconhecido": "Desconhecido",
}


def _tipo_label(v: str) -> str:
    return _TIPO_LABEL.get(v, v)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Carteira")
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

templates = Jinja2Templates(directory=str(_HERE / "templates"))
templates.env.filters["brl"] = _brl
templates.env.filters["qty"] = _qty
templates.env.filters["tipo_label"] = _tipo_label

_TIPOS = [
    "acao", "fii", "bdr", "tesouro_direto", "renda_fixa",
    "recibo_subscricao", "direito_subscricao", "desconhecido",
]
_TIPO_ORDER = _TIPOS  # same order for display


def investidor_atual() -> int:
    """The investidor whose portfolio this request reads and writes.

    Until authentication exists this is the local investidor
    (CARTEIRA_INVESTIDOR_ID, or the only one); it will come from the session.
    """
    with connect() as conn:
        return investidor_do_cli(conn)


InvestidorId = Annotated[int, Depends(investidor_atual)]


def _erro(msg: str, status_code: int = 422) -> HTMLResponse:
    return HTMLResponse(msg, status_code=status_code)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, investidor_id: InvestidorId):
    with connect() as conn:
        posicoes = calcular_posicoes(conn, investidor_id)

    grupos: dict[str, list] = defaultdict(list)
    for p in posicoes:
        if p["is_open"]:
            grupos[p["tipo"]].append(p)

    custo_total = sum(p["custo_total"] or 0.0 for p in posicoes if p["is_open"])
    rendimentos_total = sum(p["total_income"] or 0.0 for p in posicoes)

    return templates.TemplateResponse(request, "dashboard.html", {
        "grupos": [(t, grupos[t]) for t in _TIPO_ORDER if t in grupos],
        "custo_total": custo_total,
        "rendimentos_total": rendimentos_total,
    })


# ---------------------------------------------------------------------------
# Negociações
# ---------------------------------------------------------------------------

def _list_negociacoes(conn, investidor_id: int) -> list[dict]:
    rows = fetch_all(
        conn,
        """
        SELECT
            n.id, n.data, n.sentido, n.quantidade,
            n.preco_unitario, n.valor_liquido, n.taxas_proporcionais,
            COALESCE(a.ticker, a.nome) AS ativo_label,
            a.tipo
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        WHERE n.investidor_id = :investidor_id
        ORDER BY n.data DESC, n.id DESC
        """,
        investidor_id=investidor_id,
    )
    return [dict(r) for r in rows]


def _list_ativos_select(conn) -> list[dict]:
    rows = fetch_all(
        conn, "SELECT id, COALESCE(ticker, nome) AS label FROM ativos ORDER BY ticker, nome"
    )
    return [dict(r) for r in rows]


@app.get("/negociacoes", response_class=HTMLResponse)
def negociacoes_list(request: Request, investidor_id: InvestidorId):
    with connect() as conn:
        negociacoes = _list_negociacoes(conn, investidor_id)
        ativos = _list_ativos_select(conn)
    return templates.TemplateResponse(request, "negociacoes.html", {
        "negociacoes": negociacoes,
        "ativos": ativos,
        "today": date.today().isoformat(),
    })


def _ensure_manual_nota(conn, investidor_id: int, nota_id: str, data: date) -> None:
    execute(
        conn,
        """
        INSERT INTO notas
            (investidor_id, nota_id, corretora_id, doc_type, data_pregao, filename)
        VALUES (:investidor_id, :nota_id, 'manual', 'Manual', :data, 'manual')
        ON CONFLICT DO NOTHING
        """,
        investidor_id=investidor_id,
        nota_id=nota_id,
        data=data,
    )


def _next_linha(conn, investidor_id: int, nota_id: str) -> int:
    return scalar(
        conn,
        """
        SELECT COALESCE(MAX(linha_na_nota), 0) + 1 FROM negociacoes
        WHERE investidor_id = :investidor_id AND nota_id = :nota_id
          AND corretora_id = 'manual'
        """,
        investidor_id=investidor_id,
        nota_id=nota_id,
    )


@app.post("/negociacoes", response_class=HTMLResponse)
def negociacoes_create(
    request: Request,
    investidor_id: InvestidorId,
    ativo_id: int = Form(...),
    data: date = Form(...),
    sentido: str = Form(...),
    quantidade: float = Form(...),
    preco_unitario: float = Form(...),
    taxas: float = Form(default=0.0),
):
    if sentido not in ("entrada", "saida"):
        return _erro("Sentido inválido")
    if quantidade <= 0:
        return _erro("Quantidade deve ser positiva")
    if preco_unitario < 0 or taxas < 0:
        return _erro("Preço e taxas não podem ser negativos")

    valor_bruto = round(quantidade * preco_unitario, 2)
    if sentido == "entrada":
        valor_liquido = round(valor_bruto + taxas, 2)
    else:
        valor_liquido = round(valor_bruto - taxas, 2)

    nota_id = f"MANUAL-{data.isoformat()}"

    with connect() as conn:
        ativo = fetch_one(
            conn,
            "SELECT tipo, COALESCE(ticker, nome) AS label FROM ativos WHERE id = :id",
            id=ativo_id,
        )
        if not ativo:
            return _erro("Ativo não encontrado", 404)

        _ensure_manual_nota(conn, investidor_id, nota_id, data)
        linha = _next_linha(conn, investidor_id, nota_id)

        new_id = scalar(
            conn,
            """
            INSERT INTO negociacoes
                (investidor_id, nota_id, corretora_id, doc_type, linha_na_nota, ativo_id,
                 data, sentido, tipo, quantidade, preco_unitario,
                 valor_bruto, taxas_proporcionais, valor_liquido)
            VALUES
                (:investidor_id, :nota_id, 'manual', 'Manual', :linha, :ativo_id,
                 :data, :sentido, :tipo, :quantidade, :preco_unitario,
                 :valor_bruto, :taxas, :valor_liquido)
            RETURNING id
            """,
            investidor_id=investidor_id,
            nota_id=nota_id,
            linha=linha,
            ativo_id=ativo_id,
            data=data,
            sentido=sentido,
            tipo=ativo["tipo"],
            quantidade=quantidade,
            preco_unitario=preco_unitario,
            valor_bruto=valor_bruto,
            taxas=taxas,
            valor_liquido=valor_liquido,
        )

    row = {
        "id": new_id,
        "data": data,
        "sentido": sentido,
        "quantidade": quantidade,
        "preco_unitario": preco_unitario,
        "valor_liquido": valor_liquido,
        "taxas_proporcionais": taxas,
        "ativo_label": ativo["label"],
        "tipo": ativo["tipo"],
    }
    return templates.TemplateResponse(request, "partials/negociacao_row.html", {"n": row})


@app.delete("/negociacoes/{neg_id}", response_class=HTMLResponse)
def negociacoes_delete(neg_id: int, investidor_id: InvestidorId):
    with connect() as conn:
        deleted = scalar(
            conn,
            """
            DELETE FROM negociacoes
            WHERE id = :id AND investidor_id = :investidor_id
            RETURNING id
            """,
            id=neg_id,
            investidor_id=investidor_id,
        )
    if deleted is None:
        return _erro("Negociação não encontrada", 404)
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Ativos
#
# ativos is the catalog shared by all investidores. Listings are scoped to the
# investidor's own activity; editing the catalog will be restricted to
# administrators once authentication exists.
# ---------------------------------------------------------------------------

_ATIVO_ROW_SQL = """
    SELECT a.id, a.ticker, a.nome, a.tipo, a.revisado,
           COUNT(n.id) AS num_negociacoes
    FROM ativos a
    LEFT JOIN negociacoes n ON n.ativo_id = a.id AND n.investidor_id = :investidor_id
"""


def _get_ativo_row(conn, investidor_id: int, ativo_id: int) -> dict:
    row = fetch_one(
        conn,
        _ATIVO_ROW_SQL + " WHERE a.id = :ativo_id GROUP BY a.id",
        investidor_id=investidor_id,
        ativo_id=ativo_id,
    )
    return dict(row) if row else {}


@app.get("/ativos", response_class=HTMLResponse)
def ativos_list(request: Request, investidor_id: InvestidorId):
    with connect() as conn:
        rows = fetch_all(
            conn,
            _ATIVO_ROW_SQL
            + """
            WHERE a.id IN (
                SELECT ativo_id FROM negociacoes WHERE investidor_id = :investidor_id
                UNION
                SELECT ativo_id FROM b3_movimentacoes WHERE investidor_id = :investidor_id
            )
            GROUP BY a.id
            ORDER BY a.tipo, a.ticker, a.nome
            """,
            investidor_id=investidor_id,
        )
    return templates.TemplateResponse(request, "ativos.html", {
        "ativos": [dict(r) for r in rows],
        "tipos": _TIPOS,
    })


@app.get("/ativos/{ativo_id}/row", response_class=HTMLResponse)
def ativo_row(request: Request, ativo_id: int, investidor_id: InvestidorId):
    with connect() as conn:
        a = _get_ativo_row(conn, investidor_id, ativo_id)
    if not a:
        return _erro("Ativo não encontrado", 404)
    return templates.TemplateResponse(request, "partials/ativo_row.html", {"a": a, "tipos": _TIPOS})


@app.get("/ativos/{ativo_id}/edit", response_class=HTMLResponse)
def ativo_edit_form(request: Request, ativo_id: int, investidor_id: InvestidorId):
    with connect() as conn:
        a = _get_ativo_row(conn, investidor_id, ativo_id)
    if not a:
        return _erro("Ativo não encontrado", 404)
    return templates.TemplateResponse(request, "partials/ativo_edit_row.html", {"a": a, "tipos": _TIPOS})


@app.patch("/ativos/{ativo_id}", response_class=HTMLResponse)
def ativo_update(
    request: Request,
    ativo_id: int,
    investidor_id: InvestidorId,
    tipo: str = Form(...),
    nome: str = Form(default=""),
    revisado: bool = Form(default=False),
):
    if tipo not in _TIPOS:
        return _erro("Tipo inválido")
    with connect() as conn:
        execute(
            conn,
            """
            UPDATE ativos
            SET tipo = :tipo, revisado = :revisado, nome = COALESCE(NULLIF(:nome, ''), nome)
            WHERE id = :id
            """,
            tipo=tipo,
            revisado=revisado,
            nome=nome,
            id=ativo_id,
        )
        a = _get_ativo_row(conn, investidor_id, ativo_id)
    if not a:
        return _erro("Ativo não encontrado", 404)
    return templates.TemplateResponse(request, "partials/ativo_row.html", {"a": a, "tipos": _TIPOS})
