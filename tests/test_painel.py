"""What the portfolio pages show: market valuation, income, an ativo's history."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

import graficos
import painel
from carrega_b3 import carregar_arquivo
from conftest import CPF_A
from database import connect_sistema, execute
from fabricas import documento, negociacao
from loader import carregar
from test_carrega_b3 import _COLUNAS

D = Decimal
HOJE = date(2026, 10, 6)


def _notas(usuario_id, *negs, nota_id="1001"):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(CPF_A, *negs, nota_id=nota_id), f"{nota_id}.pdf")


def _b3(investidor_id, *linhas, arquivo="mov.xlsx"):
    with connect_sistema() as conn:
        carregar_arquivo(conn, investidor_id, pd.DataFrame(list(linhas), columns=_COLUNAS), arquivo)


def _precos(**precos):
    with connect_sistema() as conn:
        for ticker, preco in precos.items():
            execute(
                conn,
                "INSERT INTO cotacoes (ticker, preco) VALUES (:t, :p) "
                "ON CONFLICT (ticker) DO UPDATE SET preco = :p, atualizado_em = now()",
                t=ticker, p=preco,
            )


def _carteira(investidor_id):
    with connect_sistema() as conn:
        return painel.carteira(conn, investidor_id, HOJE)


@pytest.fixture
def carteira_basica(usuario_id, investidor_a):
    _notas(
        usuario_id,
        negociacao("PETR4", "entrada", 100, 30.0, date(2025, 1, 10), linha=1),
        negociacao("HGLG11", "entrada", 10, 160.0, date(2025, 2, 10), linha=2),
    )
    return investidor_a


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


def test_valoriza_pela_cotacao(carteira_basica):
    _precos(PETR4=D("38.45"), HGLG11=D("150"))

    c = _carteira(carteira_basica)

    acoes, fiis = c.grupos
    petr = acoes.linhas[0]
    assert (petr.valor, petr.resultado, petr.a_custo) == (D("3845.00"), D("845.00"), False)
    assert petr.resultado_pct == D("845") / D("3000")
    assert fiis.resultado == D("-100")
    assert c.patrimonio == D("5345.00")
    assert c.resultado == D("745.00")
    assert c.resultado_pct == D("745") / D("4600")
    assert petr.peso == D("3845") / D("5345")


def test_sem_cotacao_fica_pelo_custo_e_fora_do_resultado(carteira_basica):
    _precos(PETR4=D("38.45"))

    c = _carteira(carteira_basica)

    hglg = c.grupos[1].linhas[0]
    assert (hglg.valor, hglg.a_custo, hglg.resultado) == (D("1600.0"), True, None)
    assert c.resultado == D("845.00")
    assert c.custo_com_resultado == D("3000")


def test_custo_desconhecido_entra_no_patrimonio_mas_nao_no_resultado(usuario_id, investidor_a):
    _b3(investidor_a, ("Credito", "14/02/2024", "Transferência", "BBSE3 - BB SEGURIDADE", "NU", 50, "-", "-"))
    _precos(BBSE3=D("33.10"))

    c = _carteira(investidor_a)

    bbse = c.linhas[0]
    assert (bbse.custo, bbse.preco_medio, bbse.resultado) == (None, None, None)
    assert bbse.valor == D("1655.00")
    assert c.resultado is None
    assert [l.rotulo for l in c.sem_custo] == ["BBSE3"]


def test_alocacao_agrupa_renda_fixa(usuario_id, investidor_a):
    _notas(
        usuario_id,
        negociacao("PETR4", "entrada", 100, 10.0, date(2025, 1, 10), linha=1),
        negociacao("Tesouro IPCA+ 2035", "entrada", 1, 3000.0, date(2025, 1, 10), linha=2),
    )
    with connect_sistema() as conn:
        execute(conn, "UPDATE ativos SET tipo = 'tesouro_direto' WHERE ticker IS NULL")

    fatias = _carteira(investidor_a).alocacao()

    assert [(f.rotulo, f.valor) for f in fatias] == [("Ações", D("1000.0")), ("Renda fixa", D("3000.0"))]
    assert sum(f.peso for f in fatias) == 1


def test_descricao_do_indexador():
    assert painel.descricao_indexador({"indexador": "CDI", "percentual_do_indexador": D("113"), "taxa_prefixada": D("0")}) == "113% do CDI"
    assert painel.descricao_indexador({"indexador": "IPC-A", "percentual_do_indexador": D("100"), "taxa_prefixada": D("6.35")}) == "IPCA + 6,35%"
    assert painel.descricao_indexador({"indexador": "CDI", "percentual_do_indexador": D("110.5"), "taxa_prefixada": None}) == "110,5% do CDI"
    assert painel.descricao_indexador({"indexador": None}) is None


# ---------------------------------------------------------------------------
# Income
# ---------------------------------------------------------------------------


def test_janela_de_12_meses():
    assert painel.janela_12m(date(2026, 10, 6)) == (date(2025, 11, 1), date(2026, 10, 6))
    assert painel.janela_12m(date(2026, 12, 31)) == (date(2026, 1, 1), date(2026, 12, 31))


def test_proventos_por_mes_tipo_e_ativo(carteira_basica):
    _b3(
        carteira_basica,
        ("Credito", "15/03/2026", "Dividendo", "PETR4 - PETROBRAS", "NU", 100, 1.5, 150.0),
        ("Credito", "16/03/2026", "Juros Sobre Capital Próprio", "PETR4 - PETROBRAS", "NU", 100, 0.5, 50.0),
        ("Debito", "17/03/2026", "Dividendo - Cancelado", "PETR4 - PETROBRAS", "NU", 100, 0.1, 10.0),
        ("Credito", "14/04/2026", "Rendimento", "HGLG11 - CSHG LOGISTICA", "NU", 10, 1.1, 11.0),
        ("Credito", "14/04/2024", "Rendimento", "HGLG11 - CSHG LOGISTICA", "NU", 10, 1.1, 99.0),
    )
    with connect_sistema() as conn:
        prov = painel.proventos(conn, carteira_basica, date(2026, 1, 1), date(2026, 12, 31), {})

    assert prov.total == D("201.0")
    assert prov.por_tipo == {"dividendos": D("140.0"), "jcp": D("50.0"), "rendimentos": D("11.0")}
    assert len(prov.meses) == 12
    assert prov.meses[2].total == D("190.0")
    assert prov.maior_mes.inicio == date(2026, 3, 1)
    assert [(a.rotulo, a.total) for a in prov.por_ativo] == [("PETR4", D("190.0")), ("HGLG11", D("11.0"))]
    assert prov.atualizado_ate == date(2026, 4, 14)


def test_proventos_dos_12_meses_entram_na_posicao(carteira_basica):
    _b3(carteira_basica,
        ("Credito", "15/03/2026", "Dividendo", "PETR4 - PETROBRAS", "NU", 100, 1.5, 150.0),
        ("Credito", "15/03/2025", "Dividendo", "PETR4 - PETROBRAS", "NU", 100, 1.0, 100.0))

    c = _carteira(carteira_basica)

    assert c.linhas[0].proventos_12m == D("150.0")
    assert c.proventos_12m == D("150.0")


# ---------------------------------------------------------------------------
# Closed positions and one ativo
# ---------------------------------------------------------------------------


def test_encerradas_com_resultado_realizado(usuario_id, investidor_a):
    _notas(
        usuario_id,
        negociacao("TAEE11", "entrada", 100, 30.0, date(2025, 1, 10), linha=1),
        negociacao("TAEE11", "saida", 100, 42.04, date(2026, 6, 18), linha=2),
    )
    with connect_sistema() as conn:
        [e] = painel.encerradas(conn, investidor_a)

    assert (e.rotulo, e.encerrada_em) == ("TAEE11", date(2026, 6, 18))
    assert e.resultado == D("1204.00")
    assert _carteira(investidor_a).vazia


def test_historico_do_ativo(usuario_id, investidor_a):
    _notas(
        usuario_id,
        negociacao("PETR4", "entrada", 200, 29.80, date(2024, 3, 12), linha=1),
        negociacao("PETR4", "entrada", 100, 34.00, date(2024, 6, 5), linha=2),
        negociacao("PETR4", "saida", 100, 38.00, date(2025, 2, 10), linha=3),
    )
    _b3(investidor_a, ("Credito", "20/08/2024", "Dividendo", "PETR4 - PETROBRAS", "NU", 300, 0.817, 245.10))
    with connect_sistema() as conn:
        ativo_id = conn.exec_driver_sql("SELECT id FROM ativos WHERE ticker = 'PETR4'").scalar()
        a = painel.ativo(conn, investidor_a, ativo_id, HOJE)

    assert [h.rotulo for h in a.historico] == ["Venda", "Dividendo", "Compra", "Compra"]
    venda, dividendo, segunda, primeira = a.historico
    assert (primeira.qtd_apos, primeira.preco_medio_apos) == (D("200"), D("29.80"))
    assert segunda.preco_medio_apos == D("31.20")
    assert (venda.qtd_apos, venda.preco_medio_apos) == (D("200"), D("31.20"))
    assert venda.detalhe.startswith("Lucro de R$ 680,00")
    assert primeira.detalhe == "Nota 1001 · Nu Invest"
    assert dividendo.valor == D("245.10")
    assert a.proventos_por_ano == [(2024, D("245.10"))]
    assert a.linha.qtd == 200
    assert a.custodia == ["Nu Invest"]


def test_ativo_de_outra_carteira_nao_aparece(usuario_id, investidor_a, investidor_b):
    _notas(usuario_id, negociacao("PETR4", "entrada", 100, 30.0, date(2025, 1, 10)))
    with connect_sistema() as conn:
        ativo_id = conn.exec_driver_sql("SELECT id FROM ativos WHERE ticker = 'PETR4'").scalar()
        assert painel.ativo(conn, investidor_b, ativo_id, HOJE) is None


# ---------------------------------------------------------------------------
# Chart geometry
# ---------------------------------------------------------------------------


def test_colunas_empilhadas():
    c = graficos.colunas([
        ("jan", [(D(100), "s-1", "a"), (D(50), "s-2", "b")]),
        ("fev", [(D(0), "s-1", "c")]),
        ("mar", [(D(75), "s-1", "d")]),
    ], altura=220)

    assert len(c.retangulos) == 3  # empty segments are not drawn
    jan_div, jan_jcp, mar = c.retangulos
    assert jan_div.y + jan_div.altura == c.base  # anchored on the baseline
    assert jan_jcp.y + jan_jcp.altura == pytest.approx(jan_div.y - 2)  # 2px gap
    assert jan_div.altura + jan_jcp.altura == pytest.approx(180)  # tallest fills the area
    assert [v.texto for v in c.valores] == ["150", "75"]
    assert [e.texto for e in c.eixo] == ["jan", "fev", "mar"]


def test_faixa_soma_100():
    r = graficos.faixa([(D("0.25"), "s-1", "a"), (D("0.75"), "s-2", "b")])
    assert [(x.x, x.largura) for x in r] == [("0.000", "25.000"), ("25.000", "75.000")]


def test_nome_bruto_da_nota_nao_e_exibido():
    assert painel.nome_exibicao("PETR4F PN EDJ N2", "PETR4") == ""
    assert painel.nome_exibicao("Kinea Renda Imobiliária FII", "KNRI11") == "Kinea Renda Imobiliária FII"


def test_juros_de_renda_fixa_ficam_fora_do_retorno_da_renda_variavel(carteira_basica):
    _b3(carteira_basica,
        ("Credito", "15/03/2026", "Dividendo", "PETR4 - PETROBRAS", "NU", 100, 1.5, 150.0),
        ("Credito", "16/03/2026", "PAGAMENTO DE JUROS", "CDB BANCO X", "NU", 1, "-", 80.0))
    with connect_sistema() as conn:
        prov = painel.proventos(conn, carteira_basica, date(2026, 1, 1), date(2026, 12, 31))

    assert prov.total == D("230.0")
    assert prov.total_renda_variavel == D("150.0")
