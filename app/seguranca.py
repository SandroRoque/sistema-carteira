"""Request security: sessions, CSRF, same-origin writes and response headers."""

from __future__ import annotations

import re
import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

import auth
from database import connect, connect_sistema, fetch_one, scalar
from settings import atras_do_fly, cookie_secure

COOKIE_SESSAO = "sessao"
COOKIE_DISPOSITIVO = "dispositivo"  # auth.token_dispositivo; outlives the session
METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})
METODOS_COM_CORPO = frozenset({"POST", "PUT", "PATCH"})

# Request body caps, checked from Content-Length before anything is read:
# forms are tiny; only the upload page takes files.
LIMITE_CORPO_PADRAO = 1024 * 1024
LIMITES_CORPO = {"/importar": 25 * 1024 * 1024}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)


class NaoAutenticado(Exception):
    pass


class SemPermissao(Exception):
    pass


class SemCarteira(Exception):
    pass


class TermosPendentes(Exception):
    """The account has not accepted the current terms of use."""


class SomenteLeitura(SemPermissao):
    """A write attempted from the demo account."""

    def __init__(self) -> None:
        super().__init__("Esta é uma demonstração com dados fictícios: nada pode ser alterado.")


# Writes the demo account may still do: they change only its own session.
_LIVRES_NA_DEMO = re.compile(r"^/sair$|^/carteiras/\d+/selecionar$")
# What an account can reach before accepting the terms: the terms themselves,
# logging out, and its LGPD rights (export, deletion) on /conta.
_LIVRES_SEM_TERMOS = re.compile(r"^/termos$|^/sair$|^/conta(/|$)|^/privacidade$")


# ---------------------------------------------------------------------------
# Cookie
# ---------------------------------------------------------------------------

def definir_cookie_sessao(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_SESSAO,
        token,
        max_age=int(auth.DURACAO_SESSAO.total_seconds()),
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )


def definir_cookie_dispositivo(response: Response, usuario_id: int) -> None:
    response.set_cookie(
        COOKIE_DISPOSITIVO,
        auth.token_dispositivo(usuario_id),
        max_age=int(auth.DURACAO_DISPOSITIVO.total_seconds()),
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        path="/entrar",
    )


def apagar_cookie_sessao(response: Response) -> None:
    response.delete_cookie(COOKIE_SESSAO, path="/", httponly=True, secure=cookie_secure(), samesite="lax")


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _sessao_do_cookie(request: Request) -> auth.Sessao:
    with connect_sistema() as conn:
        sessao = auth.obter_sessao(conn, request.cookies.get(COOKIE_SESSAO))
    if sessao is None:
        raise NaoAutenticado()
    request.state.sessao = sessao
    return sessao


async def sessao_atual(
    request: Request, sessao: Annotated[auth.Sessao, Depends(_sessao_do_cookie)]
) -> auth.Sessao:
    """The logged-in session. Unsafe methods must also carry its CSRF token,
    either in the X-CSRF-Token header (HTMX) or a csrf_token form field."""
    if request.method not in METODOS_SEGUROS:
        enviado = request.headers.get("x-csrf-token")
        if enviado is None:
            enviado = (await request.form()).get("csrf_token")
        if not isinstance(enviado, str) or not secrets.compare_digest(enviado, sessao.csrf_token):
            raise SemPermissao("Token CSRF ausente ou inválido.")
        if sessao.demo and not _LIVRES_NA_DEMO.match(request.url.path):
            raise SomenteLeitura()
    if sessao.termos_pendentes and not _LIVRES_SEM_TERMOS.match(request.url.path):
        raise TermosPendentes()
    return sessao


Sessao = Annotated[auth.Sessao, Depends(sessao_atual)]


def admin(sessao: Sessao) -> auth.Sessao:
    if not sessao.e_admin:
        raise SemPermissao("Apenas administradores podem alterar o catálogo de ativos.")
    return sessao


SessaoAdmin = Annotated[auth.Sessao, Depends(admin)]


def investidor_atual(request: Request, sessao: Sessao) -> int:
    """The portfolio selected in this session, defaulting to the account's first.

    Ownership is checked through the tenant-scoped connection: row-level
    security hides investidores of other accounts.
    """
    with connect(sessao.usuario_id) as conn:
        if sessao.investidor_id is not None:
            if fetch_one(conn, "SELECT 1 FROM investidores WHERE id = :id", id=sessao.investidor_id):
                request.state.investidor_id = sessao.investidor_id
                return sessao.investidor_id
        primeiro = scalar(conn, "SELECT id FROM investidores ORDER BY id LIMIT 1")
    if primeiro is None:
        raise SemCarteira()
    with connect_sistema() as conn:
        auth.selecionar_investidor(conn, request.cookies[COOKIE_SESSAO], primeiro)
    # The session read for this request still has none: pages use this one.
    request.state.investidor_id = primeiro
    return primeiro


InvestidorId = Annotated[int, Depends(investidor_atual)]


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def _mesma_origem(request: Request) -> bool:
    origem = request.headers.get("origin") or request.headers.get("referer")
    if not origem:
        return False
    return urlsplit(origem).netloc == request.headers.get("host", request.url.netloc)


def _corpo_recusado(request: Request) -> Response | None:
    """411/413 for bodies without a declared size or over the route's cap.

    The server reads exactly Content-Length bytes, so checking the header
    bounds what the multipart parser will ever spool."""
    if request.method not in METODOS_COM_CORPO:
        return None
    tamanho = request.headers.get("content-length")
    if tamanho is None or not tamanho.isdigit():
        return PlainTextResponse("Tamanho do envio não informado.", status_code=411)
    if int(tamanho) > LIMITES_CORPO.get(request.url.path, LIMITE_CORPO_PADRAO):
        return PlainTextResponse("Envio grande demais.", status_code=413)
    return None


class SegurancaMiddleware(BaseHTTPMiddleware):
    """Rejects cross-origin writes and oversized bodies, and sets security
    headers on every response.

    The Origin check also covers the login and sign-up forms, which have no
    session (and therefore no CSRF token) yet.
    """

    async def dispatch(self, request: Request, call_next):
        response: Response | None
        if request.method not in METODOS_SEGUROS and not _mesma_origem(request):
            response = PlainTextResponse("Origem não permitida.", status_code=403)
        elif (response := _corpo_recusado(request)) is None:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        if not request.url.path.startswith("/static/"):
            # Pages carry financial data: keep them out of browser and proxy
            # caches, so they cannot be reopened from history after logout.
            response.headers["Cache-Control"] = "no-store"
        if cookie_secure():
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def ip_do_cliente(request: Request) -> str:
    """The client's address, for throttling.

    On Fly, the edge's Fly-Client-IP. Not request.client: uvicorn runs with
    --forwarded-allow-ips "*" there, so that comes from X-Forwarded-For, whose
    leftmost entry the client controls. Elsewhere (local runs, tests) the
    socket peer; Fly-Client-IP is then ignored, since nothing vouches for it.
    """
    if atras_do_fly() and (ip := request.headers.get("fly-client-ip")):
        return ip.strip()
    return request.client.host if request.client else "desconhecido"


def redirecionar(request: Request, destino: str) -> Response:
    """Redirect that also works for HTMX requests (which follow HX-Redirect)."""
    if request.headers.get("hx-request"):
        return Response(status_code=204, headers={"HX-Redirect": destino})
    return RedirectResponse(destino, status_code=303)
