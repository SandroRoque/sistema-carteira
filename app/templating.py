"""Jinja environment shared by every router."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

import formato
from database import connect, fetch_all

_HERE = Path(__file__).parent


TIPO_LABEL = {
    "acao": "Ações",
    "fii": "FIIs",
    "bdr": "BDRs",
    "tesouro_direto": "Tesouro Direto",
    "renda_fixa": "Renda fixa",
    "recibo_subscricao": "Recibos de Subscrição",
    "direito_subscricao": "Direitos de Subscrição",
    "desconhecido": "Desconhecido",
}

templates = Jinja2Templates(directory=str(_HERE / "templates"))
for _nome in ("brl", "brl_sinal", "pct", "qtd", "data", "data_hora", "hora", "quando", "mes_ano"):
    templates.env.filters[_nome] = getattr(formato, _nome)
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
    if sessao is not None:
        if "carteiras" not in contexto:
            contexto["carteiras"] = carteiras_do_usuario(sessao.usuario_id)
        contexto.setdefault("investidor_id", sessao.investidor_id)
    return templates.TemplateResponse(request, template, contexto, status_code=status_code)


def render_parcial(request: Request, template: str, contexto: dict, status_code: int = 200):
    """Render an HTMX fragment (no layout, no extra queries)."""
    return templates.TemplateResponse(request, template, contexto, status_code=status_code)
