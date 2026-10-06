"""Sign-up, login, account page and the LGPD data-subject rights:
export (art. 18, II and V) and deletion (art. 18, VI)."""

from __future__ import annotations

import json
from decimal import Decimal
from datetime import date, datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

import auth
import settings
from app.seguranca import (
    COOKIE_SESSAO,
    Sessao,
    apagar_cookie_sessao,
    definir_cookie_sessao,
    redirecionar,
)
from app.templating import render
from database import connect, connect_sistema, execute, fetch_all, fetch_one

router = APIRouter()

_ERRO_LOGIN = "E-mail ou senha inválidos, ou conta temporariamente bloqueada após várias tentativas."


def _entrar(request: Request, usuario_id: int) -> Response:
    with connect_sistema() as conn:
        auth.encerrar_sessao(conn, request.cookies.get(COOKIE_SESSAO))
        token = auth.criar_sessao(conn, usuario_id)
    response = redirecionar(request, "/")
    definir_cookie_sessao(response, token)
    return response


@router.get("/entrar")
def entrar_form(request: Request):
    return render(request, "entrar.html")


@router.post("/entrar")
def entrar(request: Request, email: str = Form(...), senha: str = Form(...)):
    with connect_sistema() as conn:
        usuario_id = auth.autenticar(conn, email, senha)
    if usuario_id is None:
        return render(request, "entrar.html", {"erro": _ERRO_LOGIN, "email": email}, 400)
    return _entrar(request, usuario_id)


@router.post("/demo")
def entrar_na_demo(request: Request):
    """Log in to the read-only demo account (demo.py), no password needed."""
    with connect_sistema() as conn:
        usuario_id = auth.usuario_demo(conn)
    if usuario_id is None:
        return Response(status_code=404)
    return _entrar(request, usuario_id)


@router.get("/cadastrar")
def cadastrar_form(request: Request):
    return render(request, "cadastrar.html")


@router.post("/cadastrar")
def cadastrar(
    request: Request,
    email: str = Form(...),
    senha: str = Form(...),
    senha_confirmacao: str = Form(...),
    aceite: bool = Form(default=False),
):
    if not settings.cadastro_aberto():
        return render(request, "cadastrar.html", status_code=403)
    erro = None
    if senha != senha_confirmacao:
        erro = "As senhas não conferem."
    elif not aceite:
        erro = "É preciso aceitar os termos para criar a conta."
    else:
        try:
            with connect_sistema() as conn:
                usuario_id = auth.criar_usuario(conn, email, senha)
        except auth.ErroCadastro as exc:
            erro = str(exc)
    if erro:
        return render(request, "cadastrar.html", {"erro": erro, "email": email}, 400)
    return _entrar(request, usuario_id)


@router.post("/sair")
def sair(request: Request, sessao: Sessao):
    with connect_sistema() as conn:
        auth.encerrar_sessao(conn, request.cookies.get(COOKIE_SESSAO))
    response = redirecionar(request, "/entrar")
    apagar_cookie_sessao(response)
    return response


@router.get("/privacidade")
def privacidade(request: Request):
    return render(request, "privacidade.html")


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

@router.get("/conta")
def conta(request: Request, sessao: Sessao):
    return render(request, "conta.html")


@router.post("/carteiras/{investidor_id}/selecionar")
def selecionar_carteira(request: Request, investidor_id: int, sessao: Sessao):
    with connect(sessao.usuario_id) as conn:
        existe = fetch_one(conn, "SELECT 1 FROM investidores WHERE id = :id", id=investidor_id)
    if not existe:
        return Response("Carteira não encontrada", status_code=404)
    with connect_sistema() as conn:
        auth.selecionar_investidor(conn, request.cookies[COOKIE_SESSAO], investidor_id)
    return redirecionar(request, "/")


@router.post("/carteiras/{investidor_id}/apelido")
def renomear_carteira(request: Request, investidor_id: int, sessao: Sessao, apelido: str = Form("")):
    with connect(sessao.usuario_id) as conn:
        execute(
            conn,
            "UPDATE investidores SET apelido = NULLIF(trim(:apelido), '') WHERE id = :id",
            apelido=apelido[:60],
            id=investidor_id,
        )
    return redirecionar(request, "/conta")


def _json_padrao(valor):
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        # As text: a JSON number would go through float and lose exactness.
        return str(valor)
    raise TypeError(f"não serializável: {type(valor).__name__}")


_EXPORTACAO = {
    "carteiras": "SELECT id, apelido, cpf_mascarado, criado_em FROM investidores ORDER BY id",
    "notas": "SELECT * FROM notas ORDER BY investidor_id, data_pregao, nota_id",
    "negociacoes": "SELECT * FROM negociacoes ORDER BY investidor_id, data, id",
    "arquivos_b3": "SELECT * FROM b3_arquivos_processados ORDER BY investidor_id, id",
    "movimentacoes_b3": "SELECT * FROM b3_movimentacoes ORDER BY investidor_id, data, id",
    "bonificacoes": "SELECT * FROM bonificacoes ORDER BY b3_movimentacao_id",
    # The file itself (conteudo) is never kept after processing; list the rest.
    "envios": "SELECT id, investidor_id, tipo, nome_arquivo, sha256, status, mensagem, "
              "criado_em, concluido_em FROM uploads ORDER BY id",
}


@router.get("/conta/exportar")
def exportar(sessao: Sessao):
    """Everything stored about the account, as JSON (portability)."""
    with connect_sistema() as conn:
        usuario = fetch_one(
            conn, "SELECT email, criado_em FROM usuarios WHERE id = :id", id=sessao.usuario_id
        )
    # Tenant-scoped: row-level security guarantees only this account's rows.
    with connect(sessao.usuario_id) as conn:
        dados = {nome: [dict(r) for r in fetch_all(conn, sql)] for nome, sql in _EXPORTACAO.items()}
    corpo = json.dumps(
        {"conta": dict(usuario), **dados}, default=_json_padrao, ensure_ascii=False, indent=2
    )
    return Response(
        corpo,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="carteira-dados.json"'},
    )


@router.post("/conta/excluir")
def excluir_conta(request: Request, sessao: Sessao, senha: str = Form(...)):
    with connect_sistema() as conn:
        if not auth.verificar_senha(conn, sessao.usuario_id, senha):
            return render(request, "conta.html", {"erro_exclusao": "Senha incorreta."}, 400)
        auth.excluir_usuario(conn, sessao.usuario_id)
    response = redirecionar(request, "/entrar?conta_excluida=1")
    apagar_cookie_sessao(response)
    return response
