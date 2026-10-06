"""Jinja environment shared by every router."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from database import connect, fetch_all

_HERE = Path(__file__).parent


def _brl(v: float | None) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}"


def _qty(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.4f}".rstrip("0").rstrip(".")


TIPO_LABEL = {
    "acao": "Ações",
    "fii": "FIIs",
    "bdr": "BDRs",
    "tesouro_direto": "Tesouro Direto",
    "renda_fixa": "Renda Fixa",
    "recibo_subscricao": "Recibos de Subscrição",
    "direito_subscricao": "Direitos de Subscrição",
    "desconhecido": "Desconhecido",
}

templates = Jinja2Templates(directory=str(_HERE / "templates"))
templates.env.filters["brl"] = _brl
templates.env.filters["qty"] = _qty
templates.env.filters["tipo_label"] = lambda v: TIPO_LABEL.get(v, v)


def carteiras_do_usuario(usuario_id: int) -> list[dict]:
    with connect(usuario_id) as conn:
        rows = fetch_all(
            conn,
            "SELECT id, apelido, cpf_mascarado, COALESCE(apelido, cpf_mascarado) AS label "
            "FROM investidores ORDER BY id",
        )
    return [dict(r) for r in rows]


def render(request: Request, template: str, contexto: dict | None = None, status_code: int = 200):
    """Render a full page. Logged-in pages get the session and portfolio list."""
    contexto = dict(contexto or {})
    sessao = getattr(request.state, "sessao", None)
    contexto.setdefault("sessao", sessao)
    if sessao is not None and "carteiras" not in contexto:
        contexto["carteiras"] = carteiras_do_usuario(sessao.usuario_id)
    return templates.TemplateResponse(request, template, contexto, status_code=status_code)


def render_parcial(request: Request, template: str, contexto: dict, status_code: int = 200):
    """Render an HTMX fragment (no layout, no extra queries)."""
    return templates.TemplateResponse(request, template, contexto, status_code=status_code)
