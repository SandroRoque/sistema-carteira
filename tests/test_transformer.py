"""Fee split across the trades of a nota."""

from decimal import Decimal

from transformer import _distribuir_taxas


def test_taxas_somam_exatamente_o_total():
    taxas = _distribuir_taxas([Decimal("100.00")] * 3, Decimal("0.10"))

    assert sum(taxas) == Decimal("0.10")
    assert all(isinstance(t, Decimal) for t in taxas)
    # The leftover cent goes to the largest trade (the first of equals).
    assert taxas == [Decimal("0.04"), Decimal("0.03"), Decimal("0.03")]


def test_taxas_proporcionais_ao_valor_absoluto():
    # A sale (negative) and a purchase share the fees by size, not by sign.
    taxas = _distribuir_taxas([Decimal("-300.00"), Decimal("100.00")], Decimal("1.00"))
    assert taxas == [Decimal("0.75"), Decimal("0.25")]


def test_sem_valores_divide_igualmente():
    taxas = _distribuir_taxas([Decimal(0)] * 3, Decimal("1.00"))
    assert sum(taxas) == Decimal("1.00")


def test_sem_taxas():
    assert _distribuir_taxas([Decimal("10.00")], Decimal(0)) == [Decimal(0)]
