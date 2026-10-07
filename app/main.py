"""FastAPI web app.

Run with:
    uvicorn app.main:app --reload

Routes are plain `def` (not `async def`): they do blocking database I/O,
so FastAPI runs them in its threadpool instead of on the event loop.

Every portfolio route runs on connect(usuario_id): row-level security limits
it to the logged-in account even if a query forgets its investidor_id filter.

The document import worker (importacao.py) runs as a thread in this process,
started with the app; set CARTEIRA_WORKER=false to run without it.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from decimal import ROUND_HALF_UP, Decimal
from datetime import date
from pathlib import Path

# Make project root importable when running from the project root directory.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import conta, importar, paginas
from app.seguranca import (
    InvestidorId,
    NaoAutenticado,
    SegurancaMiddleware,
    SemCarteira,
    SemPermissao,
    Sessao,
    SessaoAdmin,
    SomenteLeitura,
    TermosPendentes,
    redirecionar,
)
from app.templating import render, render_parcial
from database import connect, execute, fetch_all, fetch_one, scalar
from importacao import trabalhador, worker_habilitado

_HERE = Path(__file__).parent



@asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI):
    if worker_habilitado():
        trabalhador.iniciar()
    yield
    trabalhador.parar()


app = FastAPI(
    title="Carteira", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_ciclo_de_vida
)
app.add_middleware(SegurancaMiddleware)
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
app.include_router(conta.router)
app.include_router(importar.router)
app.include_router(paginas.router)

_TIPOS = [
    "acao", "fii", "bdr", "etf", "fundo", "tesouro_direto", "renda_fixa",
    "recibo_subscricao", "direito_subscricao", "opcao", "desconhecido",
]


@app.exception_handler(NaoAutenticado)
def _nao_autenticado(request: Request, _exc: NaoAutenticado):
    return redirecionar(request, "/entrar")


@app.exception_handler(SemPermissao)
def _sem_permissao(_request: Request, exc: SemPermissao):
    return PlainTextResponse(str(exc), status_code=403)


@app.exception_handler(SomenteLeitura)
def _somente_leitura(request: Request, exc: SomenteLeitura):
    if request.headers.get("hx-request"):
        # HTMX does not swap error responses; app.js shows this in the demo banner.
        return PlainTextResponse(str(exc), status_code=403, headers={"X-Somente-Leitura": "1"})
    return render(request, "somente_leitura.html", {"mensagem": str(exc)}, 403)


@app.exception_handler(TermosPendentes)
def _termos_pendentes(request: Request, _exc: TermosPendentes):
    return redirecionar(request, "/termos")


@app.exception_handler(SemCarteira)
def _sem_carteira(request: Request, _exc: SemCarteira):
    # No portfolio yet: the import page doubles as onboarding.
    return redirecionar(request, "/importar")


def _erro(msg: str, status_code: int = 422) -> HTMLResponse:
    return HTMLResponse(msg, status_code=status_code)


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


@app.get("/saude", response_class=PlainTextResponse)
def saude():
    """Liveness check for the platform. Does not touch the database, so the
    checks do not keep a serverless Postgres awake."""
    return "ok"


@app.get("/negociacoes", response_class=HTMLResponse)
def negociacoes_list(
    request: Request, sessao: Sessao, investidor_id: InvestidorId, ativo: int | None = None
):
    with connect(sessao.usuario_id) as conn:
        negociacoes = _list_negociacoes(conn, investidor_id)
        ativos = _list_ativos_select(conn)
    return render(request, "negociacoes.html", {
        "negociacoes": negociacoes,
        "ativos": ativos,
        "ativo_selecionado": ativo,
        "today": date.today().isoformat(),
        "investidor_id": investidor_id,
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


def _centavos(valor: Decimal) -> Decimal:
    """Money rounded to the cent, half up (round() on Decimal is half-even)."""
    return valor.quantize(Decimal("0.01"), ROUND_HALF_UP)


@app.post("/negociacoes", response_class=HTMLResponse)
def negociacoes_create(
    request: Request,
    sessao: Sessao,
    investidor_id: InvestidorId,
    ativo_id: int = Form(...),
    data: date = Form(...),
    sentido: str = Form(...),
    quantidade: Decimal = Form(...),
    preco_unitario: Decimal = Form(...),
    taxas: Decimal = Form(default=0),
):
    if sentido not in ("entrada", "saida"):
        return _erro("Sentido inválido")
    if quantidade <= 0:
        return _erro("Quantidade deve ser positiva")
    if preco_unitario < 0 or taxas < 0:
        return _erro("Preço e taxas não podem ser negativos")

    valor_bruto = _centavos(quantidade * preco_unitario)
    if sentido == "entrada":
        valor_liquido = _centavos(valor_bruto + taxas)
    else:
        valor_liquido = _centavos(valor_bruto - taxas)

    nota_id = f"MANUAL-{data.isoformat()}"

    with connect(sessao.usuario_id) as conn:
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
    return render_parcial(request, "partials/negociacao_row.html", {"n": row})


@app.delete("/negociacoes/{neg_id}", response_class=HTMLResponse)
def negociacoes_delete(neg_id: int, sessao: Sessao, investidor_id: InvestidorId):
    with connect(sessao.usuario_id) as conn:
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
# ativos is the catalog shared by all accounts. Listings are scoped to the
# portfolio's own activity; changing the catalog is restricted to admins.
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


def _ativo_row(request: Request, a: dict, sessao) -> HTMLResponse:
    return render_parcial(
        request, "partials/ativo_row.html", {"a": a, "tipos": _TIPOS, "pode_editar": sessao.e_admin}
    )


@app.get("/ativos", response_class=HTMLResponse)
def ativos_list(request: Request, sessao: Sessao, investidor_id: InvestidorId):
    with connect(sessao.usuario_id) as conn:
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
    return render(request, "ativos.html", {
        "ativos": [dict(r) for r in rows],
        "tipos": _TIPOS,
        "pode_editar": sessao.e_admin,
    })


@app.get("/ativos/{ativo_id}/row", response_class=HTMLResponse)
def ativo_row(request: Request, ativo_id: int, sessao: Sessao, investidor_id: InvestidorId):
    with connect(sessao.usuario_id) as conn:
        a = _get_ativo_row(conn, investidor_id, ativo_id)
    if not a:
        return _erro("Ativo não encontrado", 404)
    return _ativo_row(request, a, sessao)


@app.get("/ativos/{ativo_id}/edit", response_class=HTMLResponse)
def ativo_edit_form(request: Request, ativo_id: int, sessao: SessaoAdmin, investidor_id: InvestidorId):
    with connect(sessao.usuario_id) as conn:
        a = _get_ativo_row(conn, investidor_id, ativo_id)
    if not a:
        return _erro("Ativo não encontrado", 404)
    return render_parcial(request, "partials/ativo_edit_row.html", {"a": a, "tipos": _TIPOS})


@app.patch("/ativos/{ativo_id}", response_class=HTMLResponse)
def ativo_update(
    request: Request,
    ativo_id: int,
    sessao: SessaoAdmin,
    investidor_id: InvestidorId,
    tipo: str = Form(...),
    nome: str = Form(default=""),
    revisado: bool = Form(default=False),
):
    if tipo not in _TIPOS:
        return _erro("Tipo inválido")
    with connect(sessao.usuario_id) as conn:
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
    return _ativo_row(request, a, sessao)
