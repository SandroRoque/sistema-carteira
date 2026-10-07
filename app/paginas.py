"""The portfolio pages: overview, positions, one ativo, income.

Each route reads through the tenant-scoped connection and hands painel.py's
structures to a template; formatting and chart geometry stay out of here.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

import apuracao
import formato
import graficos
import irpf
import painel
import pendencias
import regras_fiscais
from app.seguranca import InvestidorId, Sessao, redirecionar
from app.templating import render
from database import connect, execute

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
        darfs = apuracao.em_aberto(apuracao.apuracao(conn, investidor_id))
    alocacao = carteira.alocacao() if not carteira.vazia else []
    return render(request, "visao_geral.html", {
        "carteira": carteira,
        "alocacao": alocacao,
        "faixa": graficos.faixa([(f.peso, f.serie, f.rotulo) for f in alocacao]),
        "proventos": prov,
        "colunas": _colunas_proventos(prov, empilhar=False),
        "retorno_12m": painel.razao(prov.total_renda_variavel, carteira.custo_renda_variavel),
        "darf": darfs[0] if darfs else None,
        "hoje": date.today(),
    })


@router.get("/posicoes", response_class=HTMLResponse)
def posicoes(
    request: Request, sessao: Sessao, investidor_id: InvestidorId,
    classe: str = "todas", encerradas: bool = False,
):
    with connect(sessao.usuario_id) as conn:
        carteira = painel.carteira(conn, investidor_id)
        fechadas = painel.encerradas(conn, investidor_id) if encerradas else []
        com_opcoes = painel.negocios_com_opcoes(conn, investidor_id)
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
        "negocios_com_opcoes": com_opcoes,
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
        "regra_conversao": regras_fiscais.CONVERSAO_CUSTO_TRANSFERIDO,
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


@router.post("/pendencias/conversoes/{mov_id}")
def resolver_conversao(
    request: Request, mov_id: int, sessao: Sessao, investidor_id: InvestidorId, ativo_origem_id: int = Form(...)
):
    try:
        with connect(sessao.usuario_id) as conn:
            pendencias.informar_conversao(conn, investidor_id, mov_id, ativo_origem_id)
    except pendencias.PendenciaInvalida as exc:
        return _pagina_pendencias(request, sessao, investidor_id, ("conversao", mov_id, str(exc)))
    return redirecionar(request, "/pendencias?resolvido=conversao")


@router.post("/pendencias/conversoes/{mov_id}/desfazer")
def desfazer_conversao(request: Request, mov_id: int, sessao: Sessao, investidor_id: InvestidorId):
    with connect(sessao.usuario_id) as conn:
        ativo_id = pendencias.desfazer_conversao(conn, investidor_id, mov_id)
    return redirecionar(request, f"/posicoes/{ativo_id}" if ativo_id else "/pendencias")


# ---------------------------------------------------------------------------
# Impostos
# ---------------------------------------------------------------------------

SICALC = "https://sicalc.receita.fazenda.gov.br/sicalc/principal"


_TEXTOS_REGRAS = {
    "ALIQUOTA_OPERACOES_COMUNS": "Ações, BDRs, units e ETFs: 15% sobre o lucro",
    "LIMITE_ISENCAO_ACOES": "Ações: isentas se as vendas de ações no mês somarem até R$ 20 mil. BDRs, ETFs e FIIs não têm essa isenção, mesmo com vendas pequenas",
    "UNITS_SEM_ISENCAO": "Units (ex.: TAEE11) não têm a isenção de ações",
    "ETF_SEM_ISENCAO": "ETFs de ações (ex.: BOVA11) não têm a isenção de ações",
    "DIREITOS_SEM_ISENCAO": "Direitos de subscrição vendidos: sem isenção",
    "ALIQUOTA_FII": "FIIs: 20% sobre o lucro, sem isenção",
    "ALIQUOTA_FIAGRO": "Fiagros: 20% sobre o lucro, como FIIs",
    "COMPENSACAO_COMUNS": "Prejuízos de ações, BDRs, units e ETFs abatem lucros futuros dessas operações",
    "COMPENSACAO_FII": "Prejuízos com FIIs só abatem lucros com FIIs",
    "COMPENSACAO_FIAGRO_COM_FII": "Prejuízos com Fiagros e FIIs abatem lucros uns dos outros",
    "FORA_DO_DARF_MENSAL": "ETFs de renda fixa (ex.: IMAB11) e fundos de infraestrutura têm outra tributação e não entram aqui",
    "IRRF_ALIQUOTA": "O 0,005% retido na fonte nas vendas é descontado do imposto",
    "DARF_MINIMO": "Imposto abaixo de R$ 10,00 não gera DARF: soma ao do mês seguinte",
    "VENCIMENTO": "Vencimento: último dia útil do mês seguinte, código 6015",
    "CONVERSAO_CUSTO_TRANSFERIDO": "Incorporações e conversões: as novas cotas ou ações herdam o custo das antigas, sem venda",
    "DAY_TRADE": "Day trade (compra e venda no mesmo dia e corretora) não é calculado",
}


def _regras() -> list[tuple[str, regras_fiscais.Regra]]:
    return [(texto, getattr(regras_fiscais, nome)) for nome, texto in _TEXTOS_REGRAS.items()]


def _regras_irpf() -> list[tuple[str, regras_fiscais.Regra]]:
    nomes = {
        "BEM_ACOES": "Ações: grupo 03, código 01",
        "BEM_UNITS": "Units: grupo 03, código 01, como ações",
        "BEM_BDR": "BDRs: grupo 04, código 04",
        "BEM_FII": "FIIs: grupo 07, código 03",
        "BEM_FIAGRO": "Fiagros: grupo 07, código 02",
        "BEM_ETF": "ETFs de ações: grupo 07, código 06",
        "BEM_ETF_RENDA_FIXA": "ETFs de renda fixa: grupo 07, código 08",
        "BEM_FUNDO_INFRA": "Fundos de infraestrutura: grupo 07, código 10",
        "ISENTO_DIVIDENDOS": "Dividendos: rendimento isento, linha 09",
        "ISENTO_RENDIMENTOS_FII": "Rendimentos de FII e Fiagro: rendimento isento, linha 99 (Outros)",
        "ISENTO_ACOES_ATE_20_MIL": "Lucro com ações em meses de vendas até R$ 20 mil: rendimento isento, linha 20",
        "EXCLUSIVO_JCP": "Juros sobre capital próprio: tributação exclusiva, linha 10",
    }
    return [(texto, getattr(regras_fiscais, nome)) for nome, texto in nomes.items()]


@router.get("/impostos/irpf", response_class=HTMLResponse)
def declaracao_anual(request: Request, sessao: Sessao, investidor_id: InvestidorId, ano: int | None = None):
    with connect(sessao.usuario_id) as conn:
        anos = irpf.anos(conn, investidor_id)
        if ano not in anos:
            ano = anos[0]
        d = irpf.declaracao(conn, investidor_id, ano)
        com_opcoes = painel.negocios_com_opcoes(conn, investidor_id)
    return render(request, "irpf.html", {
        "d": d,
        "anos": anos,
        "negocios_com_opcoes": com_opcoes,
        "regras": _regras_irpf(),
    })


@router.get("/impostos", response_class=HTMLResponse)
def impostos(request: Request, sessao: Sessao, investidor_id: InvestidorId, ano: int | None = None):
    with connect(sessao.usuario_id) as conn:
        meses = apuracao.apuracao(conn, investidor_id)
        com_opcoes = painel.negocios_com_opcoes(conn, investidor_id)
        fora_do_darf = painel.vendidos_fora_do_darf(conn, investidor_id)
    hoje = date.today()
    anos = sorted({m.mes.year for m in meses}, reverse=True)
    if ano not in anos:
        ano = anos[0] if anos else hoje.year
    do_ano = [m for m in reversed(meses) if m.mes.year == ano]
    ultimo = meses[-1] if meses else None
    return render(request, "impostos.html", {
        "meses": do_ano,
        "ano": ano,
        "anos": anos,
        "abertos": apuracao.em_aberto(meses),
        "prejuizo_comum": ultimo.prejuizo_comum_saldo if ultimo else painel.ZERO,
        "prejuizo_fii": ultimo.prejuizo_fii_saldo if ultimo else painel.ZERO,
        "acumulado": ultimo.acumulado if ultimo else painel.ZERO,
        "origem_prejuizo_comum": apuracao.origem_prejuizo(meses),
        "origem_prejuizo_fii": apuracao.origem_prejuizo(meses, fii=True),
        "origem_acumulado": apuracao.origem_acumulado(meses),
        "negocios_com_opcoes": com_opcoes,
        "fora_do_darf": fora_do_darf,
        "pago_no_ano": sum((m.valor_pago or 0 for m in meses if m.pago_em and m.pago_em.year == ano), painel.ZERO),
        "hoje": hoje,
        "mes_atual": date(hoje.year, hoje.month, 1),
        "regras": _regras(),
        "textos_regras": _TEXTOS_REGRAS,
        "codigo_darf": regras_fiscais.CODIGO_DARF_RENDA_VARIAVEL.valor,
        "sicalc": SICALC,
    })


def _mes_da_url(texto: str) -> date | None:
    try:
        ano, mes = (int(x) for x in texto.split("-"))
        return date(ano, mes, 1)
    except ValueError:
        return None


@router.post("/impostos/darfs/{mes}/pago")
def marcar_pago(request: Request, mes: str, sessao: Sessao, investidor_id: InvestidorId):
    inicio = _mes_da_url(mes)
    with connect(sessao.usuario_id) as conn:
        alvo = next((m for m in apuracao.apuracao(conn, investidor_id) if m.mes == inicio and m.darf > 0), None)
        if alvo is None:
            return HTMLResponse("Não há DARF a pagar neste mês.", status_code=404)
        execute(
            conn,
            """
            INSERT INTO darfs_pagos (investidor_id, mes, valor_pago, pago_em)
            VALUES (:i, :mes, :valor, CURRENT_DATE)
            ON CONFLICT (investidor_id, mes) DO UPDATE SET valor_pago = EXCLUDED.valor_pago, pago_em = EXCLUDED.pago_em
            """,
            i=investidor_id, mes=inicio, valor=alvo.darf,
        )
    return redirecionar(request, f"/impostos?ano={inicio.year}")


@router.post("/impostos/darfs/{mes}/desfazer")
def desfazer_pago(request: Request, mes: str, sessao: Sessao, investidor_id: InvestidorId):
    inicio = _mes_da_url(mes)
    with connect(sessao.usuario_id) as conn:
        execute(conn, "DELETE FROM darfs_pagos WHERE investidor_id = :i AND mes = :mes", i=investidor_id, mes=inicio)
    return redirecionar(request, f"/impostos?ano={inicio.year if inicio else ''}")
