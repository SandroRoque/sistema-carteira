"""Exercised subscriptions: the price paid becomes the cost of the new shares."""

from datetime import date
from decimal import Decimal

from conftest import CPF_A
from database import connect_sistema
from fabricas import documento, negociacao
from loader import carregar
from subscricoes import Credito, Exercicio, Recibo, casar
from test_pendencias import _b3, _posicao

D = Decimal


def test_exercicio_casa_com_o_credito_seguinte_da_mesma_empresa():
    exercicio = Exercicio("WXYZ", date(2025, 6, 10), D(1), D("8.40"))
    creditos = [
        Credito(1, 10, "WXYZ", date(2025, 6, 2), D(1)),  # before the exercise
        Credito(2, 11, "PETR", date(2025, 7, 21), D(1)),  # another company
        Credito(3, 10, "WXYZ", date(2025, 7, 14), D(2)),  # another quantity
        Credito(4, 10, "WXYZ", date(2025, 7, 21), D(1)),
    ]
    recibo = Recibo(50, "WXYZ", date(2025, 6, 13), D(1))

    [s] = casar([exercicio], creditos, [recibo])
    assert (s.credito.b3_id, s.custo, s.recibo) == (4, D("8.40"), recibo)


def test_credito_muito_depois_nao_e_da_subscricao():
    assert casar([Exercicio("WXYZ", date(2025, 6, 10), D(1), D("8.40"))],
                 [Credito(1, 10, "WXYZ", date(2026, 1, 5), D(1))], []) == []


def test_custo_pago_entra_no_preco_medio_e_recibo_fecha(usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("WXYZ3", "entrada", 100, 10.0, date(2025, 5, 12)), data=date(2025, 5, 12)), "1.pdf")
    _b3(investidor_a,
        ("Credito", "20/05/2025", "Direito de Subscrição", "WXYZ1 - WXYZ S/A", "NU", 1, "-", "-"),
        ("Debito", "10/06/2025", "Direitos de Subscrição - Exercido", "WXYZ1 - WXYZ S.A.", "NU", 1, 8.4, 8.4),
        ("Credito", "13/06/2025", "Recibo de Subscrição", "WXYZ9 - WXYZ S.A.", "NU", 1, "-", "-"),
        ("Credito", "21/07/2025", "Atualização", "WXYZ3 - WXYZ S.A.", "NU", 1, "-", "-"))

    acao = _posicao(investidor_a, "WXYZ3")
    assert (acao["qty"], acao["custo_total"], acao["custo_incompleto"]) == (101, D("1008.40"), False)
    assert not _posicao(investidor_a, "WXYZ9")["is_open"]
