"""Annual declaration: Bens e Direitos, income lines and the CNPJ table."""

from datetime import date
from decimal import Decimal

import pytest

import classes_b3
import cnpjs
import irpf
import regras_fiscais as R
from conftest import CPF_A
from database import connect_sistema, execute
from fabricas import documento, negociacao
from loader import carregar
from test_pendencias import _b3

D = Decimal

CLASSES = """ticker,tipo,subtipo
FIIX11,fii,
AGRO11,fii,fiagro
UNTX11,acao,unit
"""
# Invented CNPJs with valid check digits.
CNPJS = """ticker,cnpj,nome
EMPX4,11.222.333/0001-81,EMPRESA X S.A.
FIIX11,11.444.777/0001-61,FUNDO X FII
UNTX11,22.333.444/0001-04,EMPRESA U S.A.
"""


@pytest.fixture
def tabelas(tmp_path, monkeypatch):
    (tmp_path / "classes.csv").write_text(CLASSES)
    (tmp_path / "cnpjs.csv").write_text(CNPJS)
    monkeypatch.setattr(classes_b3, "ARQUIVO", tmp_path / "classes.csv")
    monkeypatch.setattr(cnpjs, "ARQUIVO", tmp_path / "cnpjs.csv")
    classes_b3._tabela.cache_clear()
    cnpjs._tabela.cache_clear()
    yield
    classes_b3._tabela.cache_clear()
    cnpjs._tabela.cache_clear()


def test_codigos_dos_bens():
    assert irpf.codigo_do_bem("acao", None) is R.BEM_ACOES
    assert irpf.codigo_do_bem("acao", "unit") is R.BEM_UNITS
    assert irpf.codigo_do_bem("bdr", None).valor == ("04", "04")
    assert irpf.codigo_do_bem("fii", None).valor == ("07", "03")
    assert irpf.codigo_do_bem("fii", "fiagro").valor == ("07", "02")
    assert irpf.codigo_do_bem("etf", "acoes").valor == ("07", "06")
    assert irpf.codigo_do_bem("etf", "renda_fixa").valor == ("07", "08")
    assert irpf.codigo_do_bem("fundo", "infra").valor == ("07", "10")
    assert irpf.codigo_do_bem("fundo", "fidc") is None  # 07-06 or 07-10: the user decides


def test_tabela_de_cnpjs_da_cvm():
    fca = [
        {"CNPJ_Companhia": "11.222.333/0001-81", "Nome_Empresarial": "EMPRESA X S.A.",
         "Valor_Mobiliario": "Ações Preferenciais", "Codigo_Negociacao": "EMPX4"},
        {"CNPJ_Companhia": "11.222.333/0001-81", "Nome_Empresarial": "EMPRESA X S.A.",
         "Valor_Mobiliario": "Debêntures", "Codigo_Negociacao": "EMPX15"},
    ]
    assert cnpjs.ler_fca(fca) == {"EMPX4": ("11.222.333/0001-81", "EMPRESA X S.A.")}
    fii = [{"Codigo_ISIN": "BRFIIXCTF001", "CNPJ_Fundo_Classe": "11444777000161", "Nome_Fundo_Classe": "FUNDO X FII"}]
    assert cnpjs.ler_fundos(fii, "CNPJ_Fundo_Classe", "Nome_Fundo_Classe") == {
        "BRFIIXCTF001": ("11.444.777/0001-61", "FUNDO X FII")}
    assert cnpjs.ticker_do_isin("BRFIIXCTF001") == "FIIX11"
    assert cnpjs.ticker_do_isin("BREMPXACNPR1") is None
    assert cnpjs.formatar("123") is None


def test_classe_de_acao_ausente_usa_a_da_mesma_empresa(tabelas):
    assert cnpjs.emissor("EMPX3") == ("11.222.333/0001-81", "EMPRESA X S.A.")
    assert cnpjs.emissor("OUTR3") is None
    assert cnpjs.emissor("EMPX11") is None  # an 11 is a unit or fund: never guessed


@pytest.fixture
def carteira(tabelas, usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A,
            negociacao("EMPX4", "entrada", 100, 20.0, date(2024, 5, 6), linha=1),
            negociacao("FIIX11", "entrada", 10, 100.0, date(2024, 5, 6), linha=2),
            data=date(2024, 5, 6)), "1.pdf")
        carregar(conn, usuario_id, documento(
            CPF_A,
            negociacao("EMPX4", "entrada", 100, 30.0, date(2025, 3, 3), linha=1),
            negociacao("FIIX11", "saida", 10, 110.0, date(2025, 3, 3), linha=2),
            negociacao("UNTX11", "entrada", 10, 50.0, date(2025, 3, 3), linha=3),
            nota_id="1002", data=date(2025, 3, 3)), "2.pdf")
    _b3(investidor_a,
        ("Credito", "15/04/2025", "Dividendo", "EMPX4 - EMPRESA X", "XP", 200, 0.5, 100.0),
        ("Credito", "15/05/2025", "Juros Sobre Capital Próprio", "EMPX4 - EMPRESA X", "XP", 200, 0.2, 34.0),
        ("Credito", "15/02/2025", "Rendimento", "FIIX11 - FUNDO X", "XP", 10, 0.9, 9.0))
    return investidor_a


def test_declaracao_do_ano(carteira):
    with connect_sistema() as conn:
        d = irpf.declaracao(conn, carteira, 2025)

    bens = {b.ticker: b for b in d.bens}
    empx = bens["EMPX4"]
    assert empx.codigo is R.BEM_ACOES and empx.cnpj == "11.222.333/0001-81"
    assert (empx.custo_anterior, empx.custo) == (D("2000.00"), D("5000.00"))
    assert empx.discriminacao.startswith("200 ações EMPX4 (EMPRESA X S.A.). Preço médio R$ 25,00.")
    # Sold during the year: declared with the previous value and zero now.
    fii = bens["FIIX11"]
    assert (fii.custo_anterior, fii.custo) == (D("1000.00"), D("0.00"))
    assert "posição encerrada" in fii.discriminacao
    assert bens["UNTX11"].codigo is R.BEM_UNITS and bens["UNTX11"].discriminacao.startswith("10 units UNTX11")
    assert [b.ticker for b in d.bens] == ["EMPX4", "UNTX11", "FIIX11"]  # program order: 03, then 07

    assert [(r.linha.valor, r.fonte, r.valor) for r in d.isentos] == [
        ("09", "EMPRESA X S.A.", D("100")),
        ("99", "FUNDO X FII", D("9")),
    ]
    assert [(r.linha.valor, r.cnpj, r.valor) for r in d.exclusivos] == [("10", "11.222.333/0001-81", D("34"))]


def test_ganho_isento_com_acoes_ate_20_mil(tabelas, usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("EMPX4", "entrada", 100, 10.0, date(2024, 5, 6)), data=date(2024, 5, 6)), "1.pdf")
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("EMPX4", "saida", 100, 12.0, date(2025, 8, 4)),
            nota_id="1002", data=date(2025, 8, 4)), "2.pdf")
        d = irpf.declaracao(conn, investidor_a, 2025)

    [linha20] = d.isentos
    assert (linha20.linha.valor, linha20.valor, linha20.tickers) == ("20", D("200.00"), ["EMPX4"])
    assert d.bens[0].custo == 0 and not d.meses_com_darf


def test_pagina_da_declaracao(carteira):
    from test_apuracao import _cliente_logado

    pagina = _cliente_logado().get("/impostos/irpf?ano=2025").text
    assert "1. Bens e Direitos" in pagina and "03 · 01" in pagina and "07 · 03" in pagina
    assert "11.222.333/0001-81" in pagina
    assert "09 · Lucros e dividendos recebidos" in pagina
    assert "10 · Juros sobre capital próprio" in pagina
    assert "Units: grupo 03, código 01" in pagina and "ainda não confirmado na fonte oficial" in pagina


def test_codigos_de_renda_fixa():
    assert irpf.codigo_do_bem("tesouro_direto", None).valor == ("04", "02")
    assert irpf.codigo_do_bem("renda_fixa", None, "CDB BANCO Y CDB123").valor == ("04", "02")
    assert irpf.codigo_do_bem("renda_fixa", None, "LCI 24C0001").valor == ("04", "03")
    assert irpf.codigo_do_bem("renda_fixa", None, "DEB EMPX") is None  # incentivized or not: the user decides


@pytest.fixture
def com_renda_fixa(usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A,
            negociacao("CDB BANCO Y CDB123", "entrada", 5, 1000.0, date(2024, 6, 3), linha=1),
            negociacao("LCI BANCO Z LCI456", "entrada", 2, 1000.0, date(2024, 6, 3), linha=2),
            data=date(2024, 6, 3)), "1.pdf")
        carregar(conn, usuario_id, documento(
            CPF_A,
            negociacao("CDB BANCO Y CDB123", "saida", 2, 1100.0, date(2025, 4, 1), linha=1),
            negociacao("Tesouro IPCA+ 2035", "entrada", 1, 3000.0, date(2025, 4, 1), linha=2),
            nota_id="1002", data=date(2025, 4, 1)), "2.pdf")
        execute(conn, """UPDATE ativos SET tipo = 'renda_fixa', ticker = NULL, emissor = 'BANCO Y',
                         cnpj_emissor = '11444777000161', indexador = 'CDI', percentual_do_indexador = 110,
                         taxa_prefixada = 0, vencimento = '2027-03-15' WHERE nome LIKE 'CDB%'""")
        execute(conn, """UPDATE ativos SET tipo = 'renda_fixa', ticker = NULL, emissor = 'BANCO Z',
                         cnpj_emissor = '22333444000104', indexador = 'IPC-A', taxa_prefixada = 6,
                         vencimento = '2028-01-10' WHERE nome LIKE 'LCI%'""")
        execute(conn, "UPDATE ativos SET tipo = 'tesouro_direto', ticker = NULL WHERE nome LIKE 'Tesouro%'")
    return investidor_a


def test_renda_fixa_e_tesouro_nos_bens(com_renda_fixa):
    with connect_sistema() as conn:
        d = irpf.declaracao(conn, com_renda_fixa, 2025)

    bens = {b.rotulo: b for b in d.bens}
    cdb = bens["CDB BANCO Y"]
    assert cdb.codigo is R.BEM_TITULOS_TRIBUTAVEIS and cdb.cnpj == "11.444.777/0001-61"
    # 2 of the 5 units were redeemed: 3/5 of the principal is still applied.
    assert (cdb.custo_anterior, cdb.custo) == (D("5000.00"), D("3000.00"))
    assert cdb.discriminacao.startswith("CDB BANCO Y, 110% do CDI, vencimento 15/03/2027. Valor aplicado.")
    lci = bens["LCI BANCO Z"]
    assert lci.codigo is R.BEM_TITULOS_ISENTOS and lci.custo == D("2000.00")
    assert lci.discriminacao.startswith("LCI BANCO Z, IPCA + 6,00%")
    tesouro = bens["Tesouro IPCA+ 2035"]
    assert tesouro.codigo is R.BEM_TITULOS_TRIBUTAVEIS and (tesouro.custo_anterior, tesouro.custo) == (0, D("3000.00"))
    assert d.tem_renda_fixa and [b.rotulo for b in d.sem_cnpj] == ["Tesouro IPCA+ 2035"]


def test_pagina_aponta_rendimentos_de_renda_fixa_para_o_informe(com_renda_fixa):
    from test_apuracao import _cliente_logado

    pagina = _cliente_logado().get("/impostos/irpf?ano=2025").text
    assert "04 · 02" in pagina and "04 · 03" in pagina
    assert "linha 06 da Tributação Exclusiva" in pagina and "linha 12 dos Isentos" in pagina
