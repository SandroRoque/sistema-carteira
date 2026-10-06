"""Upload of notas (PDF) and B3 reports (.xlsx). Processing happens in the
background worker (importacao.py); this page queues files and shows progress."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, UploadFile

import cobertura
from app.seguranca import Sessao
from app.templating import carteiras_do_usuario, render, render_parcial
from database import connect, fetch_all
from importacao import TAMANHO_MAXIMO, ArquivoRecusado, registrar, trabalhador

router = APIRouter()

MAX_ARQUIVOS_POR_ENVIO = 20
# The request body cap for this route is in seguranca.LIMITES_CORPO.

# status → (label, tag class)
_STATUS = {
    "pendente": ("Na fila", "info"),
    "processando": ("Lendo…", "info"),
    "concluido": ("Importado", "ok"),
    "duplicado": ("Já importado", ""),
    "erro": ("Não foi possível ler", "erro"),
}


def _uploads(usuario_id: int) -> list[dict]:
    with connect(usuario_id) as conn:
        rows = fetch_all(
            conn,
            """
            SELECT u.id, u.tipo, u.nome_arquivo, u.status, u.mensagem, u.criado_em,
                   COALESCE(i.apelido, i.cpf_mascarado) AS carteira
            FROM uploads u
            LEFT JOIN investidores i ON i.id = u.investidor_id
            ORDER BY u.id DESC
            LIMIT 50
            """,
        )
    return [
        dict(r) | {"status_label": _STATUS[r["status"]][0], "status_classe": _STATUS[r["status"]][1]}
        for r in rows
    ]


def _contexto_lista(usuario_id: int) -> dict:
    uploads = _uploads(usuario_id)
    return {
        "uploads": uploads,
        "em_andamento": any(u["status"] in ("pendente", "processando") for u in uploads),
    }


@router.get("/importar")
def importar_form(request: Request, sessao: Sessao):
    return _pagina(request, sessao)


@router.get("/importar/lista")
def importar_lista(request: Request, sessao: Sessao):
    return render_parcial(request, "partials/uploads.html", _contexto_lista(sessao.usuario_id))


@router.post("/importar")
def importar(
    request: Request,
    sessao: Sessao,
    arquivos: list[UploadFile],
    carteira_b3: int | None = Form(default=None),
):
    if len(arquivos) > MAX_ARQUIVOS_POR_ENVIO:
        return _pagina(
            request, sessao, [("", f"Envie no máximo {MAX_ARQUIVOS_POR_ENVIO} arquivos por vez.")], 422
        )

    # Plain def route (blocking database work runs in the threadpool), so the
    # spooled files are read synchronously. One byte over the cap is enough
    # for registrar to reject an oversized file.
    lidos = [(a.filename, a.file.read(TAMANHO_MAXIMO + 1)) for a in arquivos]
    recusados: list[tuple[str, str]] = []
    enfileirados = 0
    # One transaction per file: a rejected file does not undo the others.
    for nome, conteudo in lidos:
        try:
            with connect(sessao.usuario_id) as conn:
                if registrar(conn, sessao.usuario_id, nome, conteudo, carteira_b3) == "pendente":
                    enfileirados += 1
        except ArquivoRecusado as exc:
            recusados.append((nome or "arquivo", str(exc)))

    if enfileirados:
        trabalhador.acordar()
    return _pagina(request, sessao, recusados, 422 if recusados and not enfileirados else 200)


def _cobertura(usuario_id: int, investidor_id: int | None) -> tuple[cobertura.Cobertura | None, dict[int, str]]:
    """What the current portfolio's documents cover, and ativo labels for it."""
    if investidor_id is None:
        return None, {}
    with connect(usuario_id) as conn:
        c = cobertura.cobertura(conn, investidor_id)
        ids = [l.ativo_id for l in c.sem_nota + c.divergentes]
        rotulos = {
            r["id"]: r["ticker"] or r["nome"]
            for r in fetch_all(conn, "SELECT id, ticker, nome FROM ativos WHERE id = ANY(:ids)", ids=ids)
        } if ids else {}
    if not c.periodos and c.primeira_nota is None:
        return None, {}
    return c, rotulos


def _pagina(request: Request, sessao, recusados=(), status_code: int = 200):
    carteiras = carteiras_do_usuario(sessao.usuario_id)
    cob, rotulos = _cobertura(sessao.usuario_id, sessao.investidor_id)
    return render(
        request,
        "importar.html",
        {
            "carteiras": carteiras,
            "investidor_id": sessao.investidor_id,
            "recusados": recusados,
            "tamanho_maximo_mb": TAMANHO_MAXIMO // (1024 * 1024),
            "max_arquivos": MAX_ARQUIVOS_POR_ENVIO,
            "cobertura": cob,
            "rotulos": rotulos,
            **_contexto_lista(sessao.usuario_id),
        },
        status_code,
    )
