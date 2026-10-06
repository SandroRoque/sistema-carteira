"""Average cost replayed in date order (custo_medio.replay)."""

from datetime import date
from decimal import Decimal

import pytest

from custo_medio import Evento, replay

D1, D2, D3, D4 = date(2025, 1, 10), date(2025, 2, 10), date(2025, 3, 10), date(2025, 4, 10)


def D(valor) -> Decimal:
    return Decimal(str(valor))


def compra(data, qty, preco, nid=None):
    return Evento(data, "compra", D(qty), D(qty) * D(preco), nid)


def venda(data, qty, nid=None):
    return Evento(data, "venda", D(qty), None, nid)


def ev(data, tipo, qty, custo=None):
    return Evento(data, tipo, D(qty), None if custo is None else D(custo))


def test_venda_parcial_mantem_o_preco_medio():
    saldo = replay([compra(D1, 100, 10), compra(D2, 100, 20), venda(D3, 50)])

    assert saldo.qty == 150
    assert saldo.preco_medio == 15
    assert saldo.custo == 2250
    assert saldo.baixas[0].custo == 750


def test_zerar_posicao_reinicia_o_preco_medio():
    saldo = replay([compra(D1, 100, 10), venda(D2, 100), compra(D3, 100, 20)])

    assert saldo.preco_medio == 20


def test_ordem_de_entrada_nao_importa():
    eventos = [compra(D3, 100, 20), venda(D2, 100), compra(D1, 100, 10)]
    assert replay(eventos).preco_medio == 20


def test_compras_posteriores_nao_afetam_custo_de_venda_anterior():
    saldo = replay([compra(D1, 100, 10), venda(D2, 50, nid=7), compra(D3, 100, 40)])

    baixa = saldo.baixas[0]
    assert baixa.evento.negociacao_id == 7
    assert baixa.preco_medio == 10
    assert baixa.custo == 500


def venda_valor(data, qty, preco, nid=None, corretora="nu_invest"):
    return Evento(data, "venda", D(qty), D(qty) * D(preco), nid, (corretora, "1"))


def compra_em(data, qty, preco, nid=None, corretora="nu_invest"):
    return Evento(data, "compra", D(qty), D(qty) * D(preco), nid, (corretora, "1"))


def test_day_trade_fica_fora_da_posicao():
    saldo = replay([
        compra_em(D1, 100, 10, nid=1),
        compra_em(D2, 30, 12, nid=2),
        venda_valor(D2, 50, 13, nid=3),  # 30 day trade + 20 from the position
    ])

    [dt] = saldo.day_trades
    assert (dt.qtd, dt.custo_compra, dt.valor_venda, dt.resultado) == (30, 360, 390, 30)
    assert saldo.qty == 80
    assert saldo.preco_medio == 10  # the day's buy never entered the average
    assert saldo.baixas[0].evento.quantidade == 20
    assert saldo.baixas[0].custo == 200


def test_mesmo_dia_em_corretoras_diferentes_nao_e_day_trade():
    saldo = replay([
        compra_em(D1, 100, 10, nid=1, corretora="xp"),
        venda_valor(D1, 100, 12, nid=2, corretora="nu_invest"),
    ])

    assert saldo.day_trades == []
    assert saldo.qty == 0  # the buy is applied before the sale
    assert saldo.baixas[0].custo == 1000


def test_venda_sem_posicao_nao_tem_custo():
    saldo = replay([venda(D1, 10)])

    assert saldo.baixas[0].preco_medio is None
    assert saldo.baixas[0].custo is None
    assert saldo.qty == -10


def test_desdobro_dilui_o_preco_medio():
    saldo = replay([compra(D1, 100, 10), ev(D2, "desdobro", 100)])

    assert saldo.qty == 200
    assert saldo.preco_medio == 5
    assert saldo.custo == 1000


def test_bonificacao_com_custo_informado():
    saldo = replay([compra(D1, 100, 10), ev(D2, "bonificacao", 10, 50)])

    assert saldo.custo == 1050
    assert not saldo.tem_bonif_sem_custo


def test_bonificacao_sem_custo_e_sinalizada_ate_zerar():
    saldo = replay([compra(D1, 100, 10), ev(D2, "bonificacao", 10, None), venda(D3, 50)])
    assert saldo.preco_medio == pytest.approx(D(1000) / 110)
    assert saldo.tem_bonif_sem_custo
    assert saldo.baixas[0].tem_bonif_sem_custo

    saldo = replay([
        compra(D1, 100, 10), ev(D2, "bonificacao", 10, None), venda(D3, 110), compra(D4, 10, 10),
    ])
    assert not saldo.tem_bonif_sem_custo


def test_fracao_e_resgate_saem_pelo_preco_medio():
    saldo = replay([
        compra(D1, 100, 10), ev(D2, "fracao", 0.5), ev(D3, "resgate", 99.5),
    ])

    assert saldo.qty == 0
    assert saldo.custo == 0
    assert [b.custo for b in saldo.baixas] == [D(5), D(995)]


def test_atualizacao_igual_a_posicao_e_so_confirmacao():
    saldo = replay([compra(D1, 100, 10), ev(D2, "atualizacao", 100)])

    assert saldo.qty == 100


def test_atualizacao_diferente_da_posicao_e_credito_sem_custo():
    saldo = replay([compra(D1, 100, 10), ev(D2, "atualizacao", 20)])

    assert saldo.qty == 120
    assert saldo.custo == 1000


def test_atualizacao_compara_com_a_posicao_do_dia_anterior():
    # A purchase on the same day does not turn the confirmation into a credit.
    saldo = replay([compra(D1, 100, 10), compra(D2, 50, 10), ev(D2, "atualizacao", 100)])

    assert saldo.qty == 150


def test_transferencia_entra_pelo_preco_medio():
    saldo = replay([compra(D1, 100, 10), ev(D2, "transferencia_entrada", 50)])

    assert saldo.qty == 150
    assert saldo.preco_medio == 10


def test_so_negociacoes_marcam_origem_do_custo():
    assert not replay([ev(D1, "atualizacao", 10)]).tem_negociacoes
    assert replay([compra(D1, 1, 1)]).tem_negociacoes


def test_aritmetica_exata():
    # In float, 0.1 + 0.1 + 0.1 is 0.30000000000000004.
    saldo = replay([compra(D1, 1, "0.1"), compra(D2, 1, "0.1"), compra(D3, 1, "0.1")])

    assert saldo.custo == Decimal("0.3")
    assert saldo.preco_medio == Decimal("0.1")


def test_credito_sem_custo_em_posicao_vazia_fica_sem_preco_medio():
    saldo = replay([ev(D1, "transferencia_entrada", 12.34), ev(D2, "fracao", 0.34)])

    assert saldo.custo_desconhecido
    assert saldo.baixas[0].custo is None  # the auction's result is unknown, not a pure gain


def test_credito_sem_custo_seguido_de_compra_conta_como_custo_zero_e_sinaliza():
    saldo = replay([ev(D1, "atualizacao", 1), compra(D2, 7, "15.5"), venda(D3, 5)])

    assert saldo.custo_desconhecido
    assert saldo.baixas[0].custo_incompleto
    assert saldo.baixas[0].preco_medio == D("108.5") / 8


def test_custo_informado_resolve_credito_sem_nota():
    saldo = replay([ev(D1, "transferencia_entrada", 50, 1500)])

    assert not saldo.custo_desconhecido
    assert saldo.preco_medio == 30
