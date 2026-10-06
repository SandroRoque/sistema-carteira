"""Average cost replayed in date order (custo_medio.replay)."""

from datetime import date

import pytest

from custo_medio import Evento, replay

D1, D2, D3, D4 = date(2025, 1, 10), date(2025, 2, 10), date(2025, 3, 10), date(2025, 4, 10)


def compra(data, qty, preco, nid=None):
    return Evento(data, "compra", qty, qty * preco, nid)


def venda(data, qty, nid=None):
    return Evento(data, "venda", qty, None, nid)


def test_venda_parcial_mantem_o_preco_medio():
    saldo = replay([compra(D1, 100, 10), compra(D2, 100, 20), venda(D3, 50)])

    assert saldo.qty == 150
    assert saldo.preco_medio == pytest.approx(15)
    assert saldo.custo == pytest.approx(2250)
    assert saldo.baixas[0].custo == pytest.approx(750)


def test_zerar_posicao_reinicia_o_preco_medio():
    saldo = replay([compra(D1, 100, 10), venda(D2, 100), compra(D3, 100, 20)])

    assert saldo.preco_medio == pytest.approx(20)


def test_ordem_de_entrada_nao_importa():
    eventos = [compra(D3, 100, 20), venda(D2, 100), compra(D1, 100, 10)]
    assert replay(eventos).preco_medio == pytest.approx(20)


def test_compras_posteriores_nao_afetam_custo_de_venda_anterior():
    saldo = replay([compra(D1, 100, 10), venda(D2, 50, nid=7), compra(D3, 100, 40)])

    baixa = saldo.baixas[0]
    assert baixa.evento.negociacao_id == 7
    assert baixa.preco_medio == pytest.approx(10)
    assert baixa.custo == pytest.approx(500)


def test_compra_e_venda_no_mesmo_dia_compra_primeiro():
    saldo = replay([venda(D1, 100), compra(D1, 100, 10)])

    assert saldo.qty == 0
    assert saldo.baixas[0].custo == pytest.approx(1000)


def test_venda_sem_posicao_nao_tem_custo():
    saldo = replay([venda(D1, 10)])

    assert saldo.baixas[0].preco_medio is None
    assert saldo.baixas[0].custo is None
    assert saldo.qty == -10


def test_desdobro_dilui_o_preco_medio():
    saldo = replay([compra(D1, 100, 10), Evento(D2, "desdobro", 100)])

    assert saldo.qty == 200
    assert saldo.preco_medio == pytest.approx(5)
    assert saldo.custo == pytest.approx(1000)


def test_bonificacao_com_custo_informado():
    saldo = replay([compra(D1, 100, 10), Evento(D2, "bonificacao", 10, 10 * 5.0)])

    assert saldo.custo == pytest.approx(1050)
    assert not saldo.tem_bonif_sem_custo


def test_bonificacao_sem_custo_e_sinalizada_ate_zerar():
    saldo = replay([compra(D1, 100, 10), Evento(D2, "bonificacao", 10, None), venda(D3, 50)])
    assert saldo.preco_medio == pytest.approx(1000 / 110)
    assert saldo.tem_bonif_sem_custo
    assert saldo.baixas[0].tem_bonif_sem_custo

    saldo = replay([
        compra(D1, 100, 10), Evento(D2, "bonificacao", 10, None), venda(D3, 110), compra(D4, 10, 10),
    ])
    assert not saldo.tem_bonif_sem_custo


def test_fracao_e_resgate_saem_pelo_preco_medio():
    saldo = replay([
        compra(D1, 100, 10), Evento(D2, "fracao", 0.5), Evento(D3, "resgate", 99.5),
    ])

    assert saldo.qty == 0
    assert saldo.custo == 0
    assert [b.custo for b in saldo.baixas] == pytest.approx([5, 995])


def test_atualizacao_igual_a_posicao_e_so_confirmacao():
    saldo = replay([compra(D1, 100, 10), Evento(D2, "atualizacao", 100)])

    assert saldo.qty == 100


def test_atualizacao_diferente_da_posicao_e_credito_sem_custo():
    saldo = replay([compra(D1, 100, 10), Evento(D2, "atualizacao", 20)])

    assert saldo.qty == 120
    assert saldo.custo == pytest.approx(1000)


def test_atualizacao_compara_com_a_posicao_do_dia_anterior():
    # A purchase on the same day does not turn the confirmation into a credit.
    saldo = replay([compra(D1, 100, 10), compra(D2, 50, 10), Evento(D2, "atualizacao", 100)])

    assert saldo.qty == 150


def test_transferencia_entra_pelo_preco_medio():
    saldo = replay([compra(D1, 100, 10), Evento(D2, "transferencia_entrada", 50)])

    assert saldo.qty == 150
    assert saldo.preco_medio == pytest.approx(10)


def test_so_negociacoes_marcam_origem_do_custo():
    assert not replay([Evento(D1, "atualizacao", 10)]).tem_negociacoes
    assert replay([compra(D1, 1, 1)]).tem_negociacoes
