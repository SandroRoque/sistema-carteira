"""Monthly tax calculation. Each rule here has its source in regras_fiscais.py."""

from datetime import date
from decimal import Decimal

import pytest

import apuracao
from apuracao import Venda, apurar, categoria, vencimento
from conftest import CPF_A
from database import connect_sistema
from fabricas import documento, negociacao
from loader import carregar

D = Decimal


def venda(dia, cat, valor, custo, rotulo="X"):
    return Venda(dia, 1, rotulo, cat, D(valor), None if custo is None else D(custo))


def _mes(meses, ano, mes):
    return next(m for m in meses if m.mes == date(ano, mes, 1))


def test_categorias():
    assert categoria("acao", "PETR4") == "acao"
    assert categoria("acao", "TAEE11") == "comum"  # unit: no exemption
    assert categoria("bdr", "AAPL34") == "comum"
    assert categoria("fii", "HGLG11") == "fii"
    assert categoria("direito_subscricao", "ITSA1") == "comum"


def test_acoes_ate_20_mil_no_mes_isentas():
    [m] = apurar([venda(date(2026, 3, 10), "acao", "7000", "6840")])

    assert m.isento and m.ganho_isento == D("160")
    assert (m.base_comum, m.darf) == (0, 0)


def test_acoes_acima_de_20_mil_pagam_15():
    [m] = apurar([venda(date(2026, 6, 18), "acao", "23560", "22356")])

    assert not m.isento
    assert m.base_comum == D("1204")
    assert m.darf == D("180.60")
    assert m.vencimento == date(2026, 7, 31)


def test_limite_e_sobre_vendas_nao_sobre_lucro():
    # Exactly R$ 20.000 in sales is still exempt.
    [m] = apurar([venda(date(2026, 3, 10), "acao", "20000", "10000")])
    assert m.isento and m.darf == 0


def test_bdr_nao_tem_isencao_e_valor_abaixo_de_10_acumula():
    meses = apurar([
        venda(date(2023, 11, 14), "comum", "312.45", "285.00"),  # BDR, gain 27,45
        venda(date(2023, 12, 5), "comum", "500", "440"),  # gain 60
    ])
    nov, dez = meses

    assert nov.ir_bruto == D("4.1175")
    assert (nov.darf, nov.acumulado) == (0, D("4.12"))  # below R$ 10: carried
    assert dez.acumulado_anterior == D("4.12")
    assert dez.darf == D("13.12")  # 9,00 + 4,12
    assert dez.vencimento == date(2024, 1, 31)


def test_prejuizo_de_fii_so_compensa_fii():
    meses = apurar([
        venda(date(2026, 8, 5), "fii", "1824", "2010"),  # FII loss 186
        venda(date(2026, 8, 20), "comum", "1000", "900"),  # BDR gain 100
        venda(date(2026, 9, 15), "fii", "2812.50", "2399.90"),  # FII gain 412,60
    ])
    ago, setembro = meses

    assert ago.base_comum == D("100")  # the FII loss does not touch it
    assert ago.prejuizo_fii_saldo == D("186")
    assert setembro.prejuizo_fii_usado == D("186")
    assert setembro.base_fii == D("226.60")
    assert setembro.darf == D("45.32")
    assert setembro.vencimento == date(2026, 10, 30)


def test_prejuizo_em_mes_isento_compensa_ganho_tributavel_depois():
    meses = apurar([
        venda(date(2026, 1, 10), "acao", "5000", "6000"),  # exempt month, loss 1000
        venda(date(2026, 2, 10), "acao", "30000", "27000"),  # taxable, gain 3000
    ])
    jan, fev = meses

    assert jan.prejuizo_comum_saldo == D("1000")
    assert (fev.prejuizo_comum_usado, fev.base_comum) == (D("1000"), D("2000"))
    assert fev.darf == D("300.00")


def test_ganho_isento_nao_consome_prejuizo():
    meses = apurar([
        venda(date(2026, 1, 10), "comum", "1000", "1500"),  # loss 500
        venda(date(2026, 2, 10), "acao", "8000", "7000"),  # exempt gain 1000
    ])
    assert meses[1].prejuizo_comum_saldo == D("500")


def test_irrf_e_deduzido():
    [m] = apurar(
        [venda(date(2026, 6, 18), "acao", "23560", "22356")],
        irrf={date(2026, 6, 1): D("1.18")},
    )
    assert m.irrf_usado == D("1.18")
    assert m.darf == D("179.42")


def test_venda_sem_custo_fica_fora_e_e_sinalizada():
    [m] = apurar([venda(date(2026, 6, 18), "acao", "30000", None)])
    assert m.base_comum == 0
    assert len(m.sem_custo) == 1


@pytest.mark.parametrize("apuracao_em, vence", [
    (date(2026, 9, 1), date(2026, 10, 30)),
    (date(2025, 11, 1), date(2025, 12, 30)),  # 31/12: banks closed
    (date(2024, 2, 1), date(2024, 3, 28)),  # 29/03/2024: Sexta-feira Santa
    (date(2025, 12, 1), date(2026, 1, 30)),
    (date(2026, 10, 1), date(2026, 11, 30)),
])
def test_vencimento_ultimo_dia_util_do_mes_seguinte(apuracao_em, vence):
    assert vencimento(apuracao_em) == vence


def test_apuracao_a_partir_das_notas(usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A,
            negociacao("PETR4", "entrada", 1000, 20.0, date(2026, 1, 10), linha=1),
            negociacao("PETR4", "saida", 1000, 25.0, date(2026, 2, 10), linha=2),
            negociacao("PETR4", "entrada", 10, 25.0, date(2026, 2, 10), linha=3),
        ), "1.pdf")
        meses = apuracao.apuracao(conn, investidor_a)

    [fev] = meses
    # 10 shares bought and sold that day are day trade (not computed); the
    # regular sale is the other 990, at the January cost.
    assert (fev.vendas_acoes, fev.resultado_acoes) == (D("24750.00"), D("4950.00"))
    assert fev.darf == D("742.50")
    assert fev.day_trade == ["PETR4"]
    assert apuracao.em_aberto(meses) == [fev]


@pytest.fixture
def com_darf(usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A,
            negociacao("PETR4", "entrada", 1000, 20.0, date(2026, 1, 10), linha=1),
            negociacao("PETR4", "saida", 1000, 25.0, date(2026, 2, 10), linha=2),
        ), "1.pdf")
    return investidor_a


def _cliente_logado():
    from test_app import _cliente, _login

    c = _cliente()
    c.csrf = _login(c)
    return c


def test_pagina_mostra_darf_e_marca_pago(com_darf):
    c = _cliente_logado()

    pagina = c.get("/impostos")
    assert pagina.status_code == 200
    assert "DARF de fev/2026: R$ 750,00" in pagina.text
    assert "6015" in pagina.text and "31/03/2026" in pagina.text
    assert "DARF vencido" in c.get("/").text

    resp = c.post("/impostos/darfs/2026-02/pago", data={"csrf_token": c.csrf})
    assert resp.status_code == 200
    assert "Pago em" in resp.text and "DARF de fev/2026" not in resp.text
    assert "DARF" not in c.get("/").text.split("Proventos")[0]

    c.post("/impostos/darfs/2026-02/desfazer", data={"csrf_token": c.csrf})
    assert "DARF de fev/2026" in c.get("/impostos").text


def test_nao_marca_pago_mes_sem_darf(com_darf):
    c = _cliente_logado()
    assert c.post("/impostos/darfs/2026-01/pago", data={"csrf_token": c.csrf}).status_code == 404


def test_pagamento_e_isolado_por_conta(com_darf, investidor_b):
    from test_app import _cliente, _login

    bruno = _cliente()
    bruno.csrf = _login(bruno, "bruno@example.com")
    assert bruno.post("/impostos/darfs/2026-02/pago", data={"csrf_token": bruno.csrf}).status_code == 404
    with connect_sistema() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM darfs_pagos").scalar() == 0


def test_origem_do_prejuizo_e_do_acumulado():
    meses = apurar([
        venda(date(2023, 11, 14), "comum", "312.45", "285.00", "ABCD34"),  # 4,12 below minimum
        venda(date(2025, 1, 15), "acao", "4.20", "5.10", "WXYZ3"),
        venda(date(2025, 3, 10), "comum", "100", "150", "EFGH34"),
        venda(date(2025, 5, 10), "comum", "100", "80", "EFGH34"),  # pool covers it: no tax
    ])
    assert [m.mes for m in apuracao.origem_prejuizo(meses)] == [date(2025, 1, 1), date(2025, 3, 1)]
    assert [m.mes for m in apuracao.origem_acumulado(meses)] == [date(2023, 11, 1)]
    assert apuracao.origem_prejuizo(meses, fii=True) == []


def test_pagina_detalha_cada_venda_com_link_para_o_ativo(usuario_id, investidor_a):
    from test_pendencias import _b3

    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("ITUB3", "entrada", 10, 30.0, date(2025, 6, 10))), "1.pdf")
    _b3(investidor_a,
        ("Credito", "12/01/2026", "Leilão de Fração", "ITUB3 - ITAU", "NU", 0.33, 15.15, 5.00),
        ("Credito", "16/03/2026", "Leilão de Fração", "ITUB3 - ITAU", "NU", 0.40, 40.00, 16.00))
    c = _cliente_logado()

    pagina = c.get("/impostos").text
    assert "1 venda:" in pagina and "ITUB3 (leilão de fração)" in pagina
    # The loss card says where the loss came from.
    assert 'de <a href="/impostos?ano=2026#mes-2026-01">jan/2026</a> (ITUB3)' in pagina
    assert 'href="/posicoes/' in pagina and "Sobra de bonificação" in pagina
