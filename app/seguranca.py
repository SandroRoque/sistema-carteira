"""Request security: sessions, CSRF, same-origin writes and response headers."""

from __future__ import annotations

import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

import auth
from database import connect, connect_sistema, fetch_one, scalar
from settings import cookie_secure

COOKIE_SESSAO = "sessao"
METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})

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
                return sessao.investidor_id
        primeiro = scalar(conn, "SELECT id FROM investidores ORDER BY id LIMIT 1")
    if primeiro is None:
        raise SemCarteira()
    with connect_sistema() as conn:
        auth.selecionar_investidor(conn, request.cookies[COOKIE_SESSAO], primeiro)
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


class SegurancaMiddleware(BaseHTTPMiddleware):
    """Rejects cross-origin writes and sets security headers on every response.

    The Origin check also covers the login and sign-up forms, which have no
    session (and therefore no CSRF token) yet.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method not in METODOS_SEGUROS and not _mesma_origem(request):
            response: Response = PlainTextResponse("Origem não permitida.", status_code=403)
        else:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if cookie_secure():
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def redirecionar(request: Request, destino: str) -> Response:
    """Redirect that also works for HTMX requests (which follow HX-Redirect)."""
    if request.headers.get("hx-request"):
        return Response(status_code=204, headers={"HX-Redirect": destino})
    return RedirectResponse(destino, status_code=303)
