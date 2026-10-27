"""Sign-up, login, e-mail confirmation, new password, account page and the
LGPD data-subject rights: export (art. 18, II and V) and deletion (art. 18, VI)."""

from __future__ import annotations

import json
from decimal import Decimal
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import Response

import auth
import correio
import settings
from app.seguranca import (
    COOKIE_DISPOSITIVO,
    COOKIE_SESSAO,
    Sessao,
    apagar_cookie_sessao,
    definir_cookie_dispositivo,
    definir_cookie_sessao,
    ip_do_cliente,
    redirecionar,
)
from app.templating import render
from database import connect, connect_sistema, execute, fetch_all, fetch_one

router = APIRouter()

_ERRO_LOGIN = "E-mail ou senha inválidos. Depois de várias tentativas erradas, a conta espera alguns minutos antes de aceitar outra."
_ERRO_IP = "Muitas tentativas de login a partir desta rede. Tente de novo em alguns minutos."
_ERRO_IP_SENHA = "Muitos pedidos a partir desta rede. Tente de novo em alguns minutos."
_LINK_INVALIDO = "Este link não vale mais: ele expira, só pode ser usado uma vez e um pedido mais novo substitui os anteriores."


def _entrar(request: Request, usuario_id: int, lembrar_dispositivo: bool = False) -> Response:
    with connect_sistema() as conn:
        auth.encerrar_sessao(conn, request.cookies.get(COOKIE_SESSAO))
        token = auth.criar_sessao(conn, usuario_id)
    response = redirecionar(request, "/")
    definir_cookie_sessao(response, token)
    if lembrar_dispositivo:
        definir_cookie_dispositivo(response, usuario_id)
    return response


@router.get("/entrar")
def entrar_form(request: Request):
    return render(request, "entrar.html")


@router.post("/entrar")
def entrar(request: Request, email: str = Form(...), senha: str = Form(...)):
    ip = ip_do_cliente(request)
    with connect_sistema() as conn:
        if auth.ip_bloqueado(conn, ip):
            return render(request, "entrar.html", {"erro": _ERRO_IP, "email": email}, 429)
        dispositivo = auth.dispositivo_de(request.cookies.get(COOKIE_DISPOSITIVO))
        usuario_id = auth.autenticar(conn, email, senha, dispositivo)
        if usuario_id is None:
            auth.registrar_falha_ip(conn, ip)
    if usuario_id is None:
        return render(request, "entrar.html", {"erro": _ERRO_LOGIN, "email": email}, 400)
    return _entrar(request, usuario_id, lembrar_dispositivo=True)


@router.post("/demo")
def entrar_na_demo(request: Request):
    """Log in to the read-only demo account (demo.py), no password needed."""
    with connect_sistema() as conn:
        usuario_id = auth.usuario_demo(conn)
    if usuario_id is None:
        return Response(status_code=404)
    return _entrar(request, usuario_id)


def _enviar_confirmacao(tarefas: BackgroundTasks, email: str, token: str | None) -> None:
    if token is None:
        return
    tarefas.add_task(
        correio.enviar,
        email,
        "Confirme seu e-mail — Carteira",
        "Para começar a usar a Carteira, confirme seu e-mail abrindo este link:\n\n"
        f"{correio.link('/email/confirmar?token=' + token)}\n\n"
        "O link vale por dois dias. Se você não criou esta conta, ignore esta mensagem.\n",
    )


@router.get("/cadastrar")
def cadastrar_form(request: Request):
    return render(request, "cadastrar.html")


@router.post("/cadastrar")
def cadastrar(
    request: Request,
    tarefas: BackgroundTasks,
    email: str = Form(...),
    senha: str = Form(...),
    senha_confirmacao: str = Form(...),
    aceite: bool = Form(default=False),
):
    # Without e-mail the new account could never be confirmed.
    if not settings.cadastro_aberto() or not correio.disponivel():
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
                auth.aceitar_termos(conn, usuario_id)
                token = auth.criar_token_email(conn, usuario_id, "confirmar_email")
        except auth.ErroCadastro as exc:
            erro = str(exc)
    if erro:
        return render(request, "cadastrar.html", {"erro": erro, "email": email}, 400)
    _enviar_confirmacao(tarefas, email.strip(), token)
    return _entrar(request, usuario_id)


# ---------------------------------------------------------------------------
# E-mail confirmation
# ---------------------------------------------------------------------------

@router.get("/email/pendente")
def email_pendente(request: Request, sessao: Sessao):
    if not sessao.email_pendente:
        return redirecionar(request, "/")
    return render(request, "email_pendente.html")


@router.post("/email/reenviar")
def reenviar_confirmacao(request: Request, sessao: Sessao, tarefas: BackgroundTasks):
    if not sessao.email_pendente:
        return redirecionar(request, "/")
    if not correio.disponivel():
        return render(request, "email_pendente.html", {"erro": "O envio de e-mails está indisponível."}, 503)
    with connect_sistema() as conn:
        token = auth.criar_token_email(conn, sessao.usuario_id, "confirmar_email")
    if token is None:
        return render(
            request, "email_pendente.html",
            {"erro": "Acabamos de enviar um e-mail. Espere um minuto antes de pedir outro."}, 429,
        )
    _enviar_confirmacao(tarefas, sessao.email, token)
    return render(request, "email_pendente.html", {"reenviado": True})


@router.get("/email/confirmar")
def confirmar_email(request: Request, token: str = ""):
    """The link in the confirmation e-mail. Works in any browser, logged in or
    not: it only marks the address as confirmed."""
    with connect_sistema() as conn:
        usuario_id = auth.usar_token(conn, token[:100], "confirmar_email")
        if usuario_id is not None:
            auth.confirmar_email(conn, usuario_id)
    if usuario_id is None:
        return render(request, "email_confirmado.html", {"erro": _LINK_INVALIDO}, 400)
    return render(request, "email_confirmado.html")


# ---------------------------------------------------------------------------
# New password
# ---------------------------------------------------------------------------

@router.get("/senha/esqueci")
def esqueci_form(request: Request):
    return render(request, "esqueci_senha.html")


@router.post("/senha/esqueci")
def esqueci(request: Request, tarefas: BackgroundTasks, email: str = Form(...)):
    if not correio.disponivel():
        return render(request, "esqueci_senha.html", status_code=503)
    ip = ip_do_cliente(request)
    with connect_sistema() as conn:
        # Counted with failed logins: the same budget per network.
        if auth.ip_bloqueado(conn, ip):
            return render(request, "esqueci_senha.html", {"erro": _ERRO_IP_SENHA, "email": email}, 429)
        auth.registrar_falha_ip(conn, ip)
        pedido = auth.pedir_nova_senha(conn, email[:320])
    if pedido is not None:
        destino, token = pedido
        tarefas.add_task(
            correio.enviar,
            destino,
            "Nova senha — Carteira",
            "Alguém pediu uma nova senha para a sua conta na Carteira. Para escolher a nova senha, abra:\n\n"
            f"{correio.link('/senha/redefinir?token=' + token)}\n\n"
            "O link vale por uma hora e só pode ser usado uma vez. Se não foi você, ignore esta "
            "mensagem: sua senha continua a mesma.\n",
        )
    # The same answer whether or not the account exists.
    return render(request, "esqueci_senha.html", {"enviado": True})


@router.get("/senha/redefinir")
def redefinir_form(request: Request, token: str = ""):
    # Only checked here, not spent: mail scanners open links before people do.
    with connect_sistema() as conn:
        valido = auth.usuario_do_token(conn, token[:100], "nova_senha") is not None
    if not valido:
        return render(request, "redefinir_senha.html", {"erro_link": _LINK_INVALIDO}, 400)
    return render(request, "redefinir_senha.html", {"token": token})


@router.post("/senha/redefinir")
def redefinir(
    request: Request,
    token: str = Form(...),
    senha: str = Form(...),
    senha_confirmacao: str = Form(...),
):
    if senha != senha_confirmacao:
        return render(request, "redefinir_senha.html", {"token": token, "erro": "As senhas não conferem."}, 400)
    try:
        with connect_sistema() as conn:
            usuario_id = auth.redefinir_senha(conn, token[:100], senha)
    except auth.ErroCadastro as exc:
        return render(request, "redefinir_senha.html", {"token": token, "erro": str(exc)}, 400)
    if usuario_id is None:
        return render(request, "redefinir_senha.html", {"erro_link": _LINK_INVALIDO}, 400)
    # Every other session ended with the old password; this browser starts one.
    return _entrar(request, usuario_id, lembrar_dispositivo=True)


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


@router.get("/termos")
def termos(request: Request):
    """Public; a logged-in account that has not accepted them gets the form."""
    with connect_sistema() as conn:
        sessao = auth.obter_sessao(conn, request.cookies.get(COOKIE_SESSAO))
    if sessao is not None:
        request.state.sessao = sessao
    return render(request, "termos.html", {"versao": date.fromisoformat(auth.TERMOS_VERSAO)})


@router.post("/termos")
def aceitar_termos(request: Request, sessao: Sessao, aceito: str = Form("")):
    if aceito != "sim":
        return render(request, "termos.html", {
            "versao": date.fromisoformat(auth.TERMOS_VERSAO), "erro": "Marque a caixa para continuar.",
        }, 422)
    with connect_sistema() as conn:
        auth.aceitar_termos(conn, sessao.usuario_id)
    return redirecionar(request, "/")


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

@router.get("/conta")
def conta(request: Request, sessao: Sessao):
    return render(request, "conta.html")


_ERRO_CPF = "CPF inválido: confira os 11 dígitos."


@router.post("/carteiras")
def criar_carteira(
    request: Request,
    sessao: Sessao,
    cpf: str = Form(""),
    apelido: str = Form(""),
    voltar: str = Form("/conta"),
):
    """Create a portfolio from a CPF typed by the user, for accounts that start
    with B3 reports (which carry no CPF). Like a nota, the CPF is kept only as
    an HMAC and a mask; the same CPF twice selects the existing portfolio."""
    from contas import get_or_create_investidor

    voltar = voltar if voltar in ("/conta", "/importar") else "/conta"
    try:
        with connect(sessao.usuario_id) as conn:
            investidor_id = get_or_create_investidor(
                conn, sessao.usuario_id, cpf[:20], apelido.strip()[:60] or None
            )
    except ValueError:
        if voltar == "/importar":
            from app.importar import _pagina

            return _pagina(request, sessao, [("", _ERRO_CPF)], 422)
        return render(request, "conta.html", {"erro_carteira": _ERRO_CPF}, 422)
    with connect_sistema() as conn:
        auth.selecionar_investidor(conn, request.cookies[COOKIE_SESSAO], investidor_id)
    return redirecionar(request, voltar)


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
