"""FastAPI web app for portfolio CRUD.

Run with:
    uvicorn app.main:app --reload
"""
from __future__ import annotations

import sys
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

# Make project root importable when running from the project root directory.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import connect, init_db
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Carteira", lifespan=lifespan)
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


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    with connect() as conn:
        posicoes = calcular_posicoes(conn)

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

def _list_negociacoes(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            n.id, n.data, n.sentido, n.quantidade,
            n.preco_unitario, n.valor_liquido, n.taxas_proporcionais,
            COALESCE(a.ticker, a.nome) AS ativo_label,
            a.tipo
        FROM negociacoes n
        JOIN ativos a ON a.id = n.ativo_id
        ORDER BY n.data DESC, n.id DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _list_ativos_select(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, COALESCE(ticker, nome) AS label FROM ativos ORDER BY ticker, nome"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/negociacoes", response_class=HTMLResponse)
async def negociacoes_list(request: Request):
    with connect() as conn:
        negociacoes = _list_negociacoes(conn)
        ativos = _list_ativos_select(conn)
    return templates.TemplateResponse(request, "negociacoes.html", {
        "negociacoes": negociacoes,
        "ativos": ativos,
        "today": date.today().isoformat(),
    })


def _ensure_manual_nota(conn, nota_id: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO notas
            (nota_id, corretora_id, doc_type, data_pregao,
             cpf_cliente, codigo_cliente, filename)
        VALUES (?, 'manual', 'Manual', date('now'), '', '', 'manual')
        """,
        (nota_id,),
    )


def _next_linha(conn, nota_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(linha_na_nota), 0) + 1 FROM negociacoes "
        "WHERE nota_id = ? AND corretora_id = 'manual'",
        (nota_id,),
    ).fetchone()
    return row[0]


@app.post("/negociacoes", response_class=HTMLResponse)
async def negociacoes_create(
    request: Request,
    ativo_id: int = Form(...),
    data: str = Form(...),
    sentido: str = Form(...),
    quantidade: float = Form(...),
    preco_unitario: float = Form(...),
    taxas: float = Form(default=0.0),
):
    valor_bruto = round(quantidade * preco_unitario, 2)
    if sentido == "entrada":
        valor_liquido = round(valor_bruto + taxas, 2)
    else:
        valor_liquido = round(valor_bruto - taxas, 2)

    nota_id = f"MANUAL-{data}"

    with connect() as conn:
        ativo = conn.execute(
            "SELECT tipo, COALESCE(ticker, nome) AS label FROM ativos WHERE id = ?",
            (ativo_id,),
        ).fetchone()
        if not ativo:
            return HTMLResponse("Ativo não encontrado", status_code=404)

        _ensure_manual_nota(conn, nota_id)
        linha = _next_linha(conn, nota_id)

        conn.execute(
            """
            INSERT INTO negociacoes
                (nota_id, corretora_id, doc_type, linha_na_nota, ativo_id,
                 data, sentido, tipo, quantidade, preco_unitario,
                 valor_bruto, taxas_proporcionais, valor_liquido)
            VALUES (?, 'manual', 'Manual', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (nota_id, linha, ativo_id, data, sentido, ativo["tipo"],
             quantidade, preco_unitario, valor_bruto, taxas, valor_liquido),
        )
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

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
async def negociacoes_delete(neg_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM negociacoes WHERE id = ?", (neg_id,))
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Ativos
# ---------------------------------------------------------------------------

def _get_ativo_row(conn, ativo_id: int) -> dict:
    row = conn.execute(
        """
        SELECT a.id, a.ticker, a.nome, a.tipo, a.revisado,
               COUNT(n.id) AS num_negociacoes
        FROM ativos a
        LEFT JOIN negociacoes n ON n.ativo_id = a.id
        WHERE a.id = ?
        GROUP BY a.id
        """,
        (ativo_id,),
    ).fetchone()
    return dict(row) if row else {}


@app.get("/ativos", response_class=HTMLResponse)
async def ativos_list(request: Request):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.ticker, a.nome, a.tipo, a.revisado,
                   COUNT(n.id) AS num_negociacoes
            FROM ativos a
            LEFT JOIN negociacoes n ON n.ativo_id = a.id
            GROUP BY a.id
            ORDER BY a.tipo, a.ticker, a.nome
            """
        ).fetchall()
    return templates.TemplateResponse(request, "ativos.html", {
        "ativos": [dict(r) for r in rows],
        "tipos": _TIPOS,
    })


@app.get("/ativos/{ativo_id}/row", response_class=HTMLResponse)
async def ativo_row(request: Request, ativo_id: int):
    with connect() as conn:
        a = _get_ativo_row(conn, ativo_id)
    return templates.TemplateResponse(request, "partials/ativo_row.html", {"a": a, "tipos": _TIPOS})


@app.get("/ativos/{ativo_id}/edit", response_class=HTMLResponse)
async def ativo_edit_form(request: Request, ativo_id: int):
    with connect() as conn:
        a = _get_ativo_row(conn, ativo_id)
    return templates.TemplateResponse(request, "partials/ativo_edit_row.html", {"a": a, "tipos": _TIPOS})


@app.patch("/ativos/{ativo_id}", response_class=HTMLResponse)
async def ativo_update(
    request: Request,
    ativo_id: int,
    tipo: str = Form(...),
    nome: str = Form(default=""),
    revisado: int = Form(default=0),
):
    with connect() as conn:
        conn.execute(
            "UPDATE ativos SET tipo = ?, revisado = ? WHERE id = ?",
            (tipo, revisado, ativo_id),
        )
        if nome:
            conn.execute("UPDATE ativos SET nome = ? WHERE id = ?", (nome, ativo_id))
        a = _get_ativo_row(conn, ativo_id)
    return templates.TemplateResponse(request, "partials/ativo_row.html", {"a": a, "tipos": _TIPOS})
