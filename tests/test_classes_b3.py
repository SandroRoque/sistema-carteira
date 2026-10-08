"""Class of tickers ending in 11 (unit, ETF, FII, Fiagro, other fund) from B3 data."""

from datetime import date
from decimal import Decimal

import pytest

import apuracao
import classes_b3 as cb3
import painel
from conftest import CPF_A
from database import connect_sistema, fetch_all
from fabricas import documento, negociacao
from loader import carregar

D = Decimal

TABELA = """ticker,tipo,subtipo
UNTX11,acao,unit
ETFA11,etf,acoes
ETFR11,etf,renda_fixa
FIIX11,fii,
AGRO11,fii,fiagro
INFR11,fundo,infra
"""


@pytest.fixture
def tabela(tmp_path, monkeypatch):
    arquivo = tmp_path / "classes_b3.csv"
    arquivo.write_text(TABELA)
    monkeypatch.setattr(cb3, "ARQUIVO", arquivo)
    cb3._tabela.cache_clear()
    yield
    cb3._tabela.cache_clear()


@pytest.mark.parametrize("nome, grupo, esperado", [
    ("GALPOES Y - FDO INV IMOB - RESPONSABILIDADE LTDA.", "12", ("fii", None)),
    ("FUNDO X SEM PISTA NO NOME", "12", ("fii", None)),  # B3's FII market group decides
    ("KINEA CRÉDITO AGRO FIAGRO RESP LIM", "14", ("fii", "fiagro")),
    ("NEX CREDITO AGRO FI CAD PROD AGRO FIAG IMOB R", "14", ("fii", "fiagro")),
    ("CAURIS FUNDO DE INV EM COTAS DE FUNDO DE FIDC", "14", ("fundo", "fidc")),
    ("AZ QUEST INFRA-YIELD II FIP IE", "14", ("fundo", "fip")),
    ("BTG PACTUAL DIVIDA INFRA FIC. FDO. INC. IE. R", "14", ("fundo", "infra")),
    ("SPIM FUNDO DE INVESTIMENTO IMOBILIÁRIO", None, ("fii", None)),
    ("FDO INV DO NORDESTE", "14", ("fundo", None)),
])
def test_fundo_pelo_nome_e_grupo(nome, grupo, esperado):
    assert cb3.fundo(nome, grupo) == esperado


def test_classe_pela_categoria_do_cadastro():
    assert cb3.do_cadastro("UNIT", "EMPRESA S.A.", "02", False) == ("acao", "unit")
    assert cb3.do_cadastro("ETF EQUITIES", "ISHARES X FUNDO DE ÍNDICE", "14", False) == ("etf", "acoes")
    assert cb3.do_cadastro("ETF FOREIGN INDEX", "FUNDO DE ÍNDICE Y", "14", False) == ("etf", "exterior")
    assert cb3.do_cadastro("FIXED INCOME TRADABLE INSTRUMENT T1", "", None, True) == ("etf", "renda_fixa")
    assert cb3.do_cadastro("FUNDS", "Z FDO INV IMOB", "12", False) == ("fii", None)
    assert cb3.do_cadastro("WARRANT", "EMPRESA S.A.", None, False) is None


def test_classe_do_historico():
    assert cb3.do_historico("EMPRESA", "UNT", "02") == ("acao", "unit")
    assert cb3.do_historico("FII ALGUM", "CI", "12") == ("fii", None)
    assert cb3.do_historico("ETF ANTIGO", "CI", "14") == ("fundo", None)
    assert cb3.do_historico("EMPRESA", "ON", "02") is None


def test_le_o_cadastro_de_instrumentos():
    colunas = "RptDt;TckrSymb;Asst;AsstDesc;SgmtNm;MktNm;SctyCtgyNm;CrpnNm"
    texto = "\n".join([
        "Status do Arquivo: Final",
        colunas,
        "2026-10-05;ETFA11;ETFA;ETFA;CASH;EQUITY-CASH;ETF EQUITIES;ETF A FUNDO DE INDICE",
        "2026-10-05;ETFR11;ETFR;ETFR;FORWARD;FIXED INCOME;FIXED INCOME TRADABLE INSTRUMENT T1;",
        "2026-10-05;ETFRETF11H;ETFR;ETFR;ETF PRIMARY MARKET;FIXED INCOME;ETF PRIMARY MARKET GROSS SETTLEMENT;",
        "2026-10-05;DEBX11;DEBX;DEBX;FORWARD;FIXED INCOME;FIXED INCOME TRADABLE INSTRUMENT T1;",
        "2026-10-05;EMPX3;EMPX;EMPX;CASH;EQUITY-CASH;SHARES;EMPRESA X S.A.",
    ])
    assert cb3.ler_cadastro(texto) == {
        "ETFA11": ("ETF EQUITIES", "ETF A FUNDO DE INDICE"),
        "ETFR11": ("ETF RENDA FIXA", ""),
    }


def test_carga_classifica_pelo_tabela_da_b3(tabela, usuario_id):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A,
            *(negociacao(t, linha=i) for i, t in enumerate(
                ["UNTX11", "ETFA11", "AGRO11", "INFR11", "NOVO11", "EMPX3"], start=1)),
        ), "1.pdf")
        ativos = {r["ticker"]: (r["tipo"], r["subtipo"])
                  for r in fetch_all(conn, "SELECT ticker, tipo, subtipo FROM ativos")}
    assert ativos == {
        "UNTX11": ("acao", "unit"),
        "ETFA11": ("etf", "acoes"),
        "AGRO11": ("fii", "fiagro"),
        "INFR11": ("fundo", "infra"),
        "NOVO11": ("fii", None),  # not in B3's table: the suffix decides
        "EMPX3": ("acao", None),
    }


def test_categorias_de_imposto():
    assert apuracao.categoria("acao", "UNTX11", "unit") == "comum"
    assert apuracao.categoria("etf", "ETFA11", "acoes") == "comum"
    assert apuracao.categoria("etf", "ETFE11", "exterior") == "comum"
    assert apuracao.categoria("fii", "AGRO11", "fiagro") == "fii"
    assert apuracao.categoria("etf", "ETFR11", "renda_fixa") is None
    assert apuracao.categoria("fundo", "INFR11", "infra") is None


def test_etf_paga_sem_isencao_e_renda_fixa_fica_fora(tabela, usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A,
            negociacao("ETFA11", "entrada", 100, 50.0, date(2026, 1, 12), linha=1),
            negociacao("ETFR11", "entrada", 100, 80.0, date(2026, 1, 12), linha=2),
            negociacao("INFR11", "entrada", 100, 90.0, date(2026, 1, 12), linha=3),
            data=date(2026, 1, 12)), "1.pdf")
        carregar(conn, usuario_id, documento(
            CPF_A,
            negociacao("ETFA11", "saida", 100, 60.0, date(2026, 2, 9), linha=1),
            negociacao("ETFR11", "saida", 100, 90.0, date(2026, 2, 9), linha=2),
            negociacao("INFR11", "saida", 100, 95.0, date(2026, 2, 9), linha=3),
            nota_id="1002", data=date(2026, 2, 9)), "2.pdf")
        [fev] = apuracao.apuracao(conn, investidor_a)
        fora = painel.vendidos_fora_do_darf(conn, investidor_a)

    # R$ 6.000 of ETF sales is far below R$ 20 mil, and still taxed at 15%.
    assert [v.rotulo for v in fev.vendas] == ["ETFA11"]
    assert fev.base_comum == D("1000.00")
    assert fev.darf == D("150.00")
    assert fora == ["ETFR11", "INFR11"]

    from test_apuracao import _cliente_logado

    pagina = _cliente_logado().get("/impostos").text
    assert "Vendas de ETFR11, INFR11 não entram nesta apuração" in pagina
    assert "ETFs de ações (ex.: BOVA11) não têm a isenção de ações" in pagina
