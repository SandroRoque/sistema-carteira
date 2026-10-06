"""The portfolio pages: overview, positions, one ativo, income.

Each route reads through the tenant-scoped connection and hands painel.py's
structures to a template; formatting and chart geometry stay out of here.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

import formato
import graficos
import painel
import pendencias
from app.seguranca import InvestidorId, Sessao, redirecionar
from app.templating import render
from database import connect

router = APIRouter()

def _colunas_proventos(prov: painel.Proventos, empilhar: bool) -> graficos.Colunas:
    grupos = []
    for m in prov.meses:
        rotulo = formato.MESES[m.inicio.month - 1]
        nome = f"{rotulo}/{m.inicio.year}"
        if empilhar:
            # Bottom to top in the legend's order.
            segs = [
                (m.valores.get(t, painel.ZERO), painel.TIPOS_PROVENTO[t][1],
                 f"{nome} · {painel.TIPOS_PROVENTO[t][0]}: {formato.brl(m.valores.get(t, painel.ZERO))}")
                for t in prov.tipos
            ]
        else:
            segs = [(m.total, "s-1", f"{nome}: {formato.brl(m.total)}")]
        grupos.append((rotulo, segs))
    return graficos.colunas(grupos)


@router.get("/", response_class=HTMLResponse)
def visao_geral(request: Request, sessao: Sessao, investidor_id: InvestidorId):
    with connect(sessao.usuario_id) as conn:
        carteira = painel.carteira(conn, investidor_id)
        inicio, fim = painel.janela_12m()
        prov = painel.proventos(conn, investidor_id, inicio, fim)
    alocacao = carteira.alocacao() if not carteira.vazia else []
    return render(request, "visao_geral.html", {
        "carteira": carteira,
        "alocacao": alocacao,
        "faixa": graficos.faixa([(f.peso, f.serie, f.rotulo) for f in alocacao]),
        "proventos": prov,
        "colunas": _colunas_proventos(prov, empilhar=False),
        "retorno_12m": painel.razao(prov.total_renda_variavel, carteira.custo_renda_variavel),
    })


@router.get("/posicoes", response_class=HTMLResponse)
def posicoes(
    request: Request, sessao: Sessao, investidor_id: InvestidorId,
    classe: str = "todas", encerradas: bool = False,
):
    with connect(sessao.usuario_id) as conn:
        carteira = painel.carteira(conn, investidor_id)
        fechadas = painel.encerradas(conn, investidor_id) if encerradas else []
    classes = [(g.classe, g.rotulo) for g in carteira.grupos]
    if classe not in {c for c, _ in classes}:
        classe = "todas"
    return render(request, "posicoes.html", {
        "carteira": carteira,
        "grupos": [g for g in carteira.grupos if classe in ("todas", g.classe)],
        "classes": classes,
        "classe": classe,
        "encerradas": encerradas,
        "fechadas": fechadas,
    })


@router.get("/posicoes/{ativo_id}", response_class=HTMLResponse)
def ativo(
    request: Request, ativo_id: int, sessao: Sessao, investidor_id: InvestidorId, mostrar: str = "operacoes"
):
    with connect(sessao.usuario_id) as conn:
        dados = painel.ativo(conn, investidor_id, ativo_id)
    if dados is None:
        return HTMLResponse("Ativo não encontrado nesta carteira.", status_code=404)
    # Operations by default: income entries would drown how the average price formed.
    if mostrar not in ("tudo", "operacoes", "proventos"):
        mostrar = "operacoes"
    historico = [
        h for h in dados.historico
        if mostrar == "tudo" or (mostrar == "proventos") == (h.categoria == "provento")
    ]
    maior_ano = max((v for _, v in dados.proventos_por_ano), default=painel.ZERO)
    return render(request, "ativo.html", {
        "a": dados,
        "historico": historico,
        "mostrar": mostrar,
        "anos": [(ano, v, float(v / maior_ano * 100) if maior_ano else 0) for ano, v in dados.proventos_por_ano],
    })


@router.get("/proventos", response_class=HTMLResponse)
def proventos(request: Request, sessao: Sessao, investidor_id: InvestidorId, periodo: str = "12m"):
    with connect(sessao.usuario_id) as conn:
        anos = painel.anos_com_proventos(conn, investidor_id)
        if periodo.isdigit() and int(periodo) in anos:
            inicio, fim = date(int(periodo), 1, 1), date(int(periodo), 12, 31)
        else:
            periodo = "12m"
            inicio, fim = painel.janela_12m()
        carteira = painel.carteira(conn, investidor_id)
        custos = {l.ativo_id: l.custo for l in carteira.linhas if l.custo}
        prov = painel.proventos(conn, investidor_id, inicio, fim, custos)
    return render(request, "proventos.html", {
        "prov": prov,
        "periodo": periodo,
        "periodos": [("12m", "Últimos 12 meses")] + [(str(a), str(a)) for a in anos],
        "colunas": _colunas_proventos(prov, empilhar=True),
        "tipos_provento": painel.TIPOS_PROVENTO,
        "custo_rv": carteira.custo_renda_variavel,
        "retorno": (
            painel.razao(prov.total_renda_variavel, carteira.custo_renda_variavel) if periodo == "12m" else None
        ),
    })


# ---------------------------------------------------------------------------
# Pendências
# ---------------------------------------------------------------------------


def _pagina_pendencias(request: Request, sessao, investidor_id: int, erro: tuple[str, int, str] | None = None):
    with connect(sessao.usuario_id) as conn:
        itens = pendencias.listar(conn, investidor_id)
        avisos = pendencias.avisos(conn, investidor_id)
    return render(request, "pendencias.html", {
        "pendencias": itens,
        "avisos": avisos,
        "n_pendencias": len(itens),
        "erro": erro,
        "resolvido": request.query_params.get("resolvido"),
    }, 422 if erro else 200)


@router.get("/pendencias", response_class=HTMLResponse)
def pendencias_lista(request: Request, sessao: Sessao, investidor_id: InvestidorId):
    return _pagina_pendencias(request, sessao, investidor_id)


def _resolver(request, sessao, investidor_id, tipo: str, chave: int, texto: str, acao) -> HTMLResponse:
    try:
        custo = formato.ler_decimal(texto)
        with connect(sessao.usuario_id) as conn:
            acao(conn, investidor_id, chave, custo)
    except (ValueError, pendencias.PendenciaInvalida) as exc:
        mensagem = str(exc) if isinstance(exc, pendencias.PendenciaInvalida) else "Informe um valor como 18,04."
        return _pagina_pendencias(request, sessao, investidor_id, (tipo, chave, mensagem))
    return redirecionar(request, f"/pendencias?resolvido={tipo}")


@router.post("/pendencias/bonificacoes/{mov_id}")
def resolver_bonificacao(
    request: Request, mov_id: int, sessao: Sessao, investidor_id: InvestidorId, custo_por_cota: str = Form(...)
):
    return _resolver(request, sessao, investidor_id, "bonificacao", mov_id, custo_por_cota,
                     pendencias.informar_custo_bonificacao)


@router.post("/pendencias/custos/{ativo_id}")
def resolver_custo(
    request: Request, ativo_id: int, sessao: Sessao, investidor_id: InvestidorId, custo_por_cota: str = Form(...)
):
    return _resolver(request, sessao, investidor_id, "sem_custo", ativo_id, custo_por_cota,
                     pendencias.informar_custo_sem_nota)
