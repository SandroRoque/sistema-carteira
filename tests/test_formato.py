from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import formato

D = Decimal


@pytest.mark.parametrize("valor, esperado", [
    (D("1234.5"), "R$ 1.234,50"),
    (D("0"), "R$ 0,00"),
    (D("-1080"), "− R$ 1.080,00"),
    (D("1234567.891"), "R$ 1.234.567,89"),
    (D("30.025"), "R$ 30,03"),  # half up, not half even
    (D("-0.001"), "R$ 0,00"),  # rounds to zero: no sign
    (None, "—"),
])
def test_brl(valor, esperado):
    assert formato.brl(valor) == esperado


def test_brl_sinal():
    assert formato.brl_sinal(D("2175")) == "+ R$ 2.175,00"
    assert formato.brl_sinal(D("-27")) == "− R$ 27,00"
    assert formato.brl_sinal(D("0.004")) == "R$ 0,00"


def test_pct():
    assert formato.pct(D("0.124")) == "12,4%"
    assert formato.pct(D("-0.105"), sinal=True) == "−10,5%"
    assert formato.pct(D("0.2323"), sinal=True) == "+23,2%"
    assert formato.pct(D("0.44"), casas=0) == "44%"
    assert formato.pct(None) == "—"


def test_qtd():
    assert formato.qtd(D("1250")) == "1.250"
    assert formato.qtd(D("12.3400")) == "12,34"
    assert formato.qtd(D("0.79")) == "0,79"
    assert formato.qtd(D("-10")) == "−10"


def test_datas():
    assert formato.data(date(2026, 10, 6)) == "06/10/2026"
    utc = datetime(2026, 10, 6, 18, 42, tzinfo=timezone.utc)
    assert formato.data_hora(utc) == "06/10/2026 15:42"  # Brasília
    assert formato.mes_ano(date(2025, 12, 1)) == "dez/2025"


def test_quando():
    v = datetime(2026, 10, 6, 18, 42, tzinfo=timezone.utc)
    assert formato.quando(v, hoje=date(2026, 10, 6)) == "hoje, 15:42"
    assert formato.quando(v, hoje=date(2026, 10, 7)) == "06/10/2026 15:42"


@pytest.mark.parametrize("texto, esperado", [
    ("18,04", D("18.04")), ("1.234,56", D("1234.56")), ("R$ 2,5", D("2.5")),
    ("18.04", D("18.04")), ("1.234", D("1234")), ("0", D("0")),
])
def test_ler_decimal(texto, esperado):
    assert formato.ler_decimal(texto) == esperado


@pytest.mark.parametrize("texto", ["", "abc", "NaN", "Infinity", "1,2,3"])
def test_ler_decimal_recusa(texto):
    with pytest.raises(ValueError):
        formato.ler_decimal(texto)
