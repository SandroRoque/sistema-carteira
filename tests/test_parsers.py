from datetime import date

import pytest

from parsers import br_date_parser, br_number_parser, cpf_parser, money_parser, percent_parser


def test_br_date_parser_parses_valid_date():
    assert br_date_parser("26/03/2025") == date(2025, 3, 26)


def test_br_date_parser_rejects_invalid_date():
    with pytest.raises(ValueError):
        br_date_parser("2025-03-26")


def test_br_number_parser_parses_brazilian_number():
    assert br_number_parser("1.234,56") == 1234.56


def test_br_number_parser_returns_float_for_integer_like_input():
    value = br_number_parser("14")
    assert value == 14.0
    assert isinstance(value, float)


def test_br_number_parser_parses_percent_value():
    assert br_number_parser("110.50 %") == 110.5


def test_br_number_parser_returns_none_for_blank_or_dash():
    assert br_number_parser("-") is None


def test_br_number_parser_rejects_invalid_number():
    with pytest.raises(ValueError):
        br_number_parser("abc")


def test_money_parser_parses_brazilian_money():
    assert money_parser("2.000,00") == 2000.0


def test_money_parser_parses_brazilian_money_with_currency_symbol():
    assert money_parser("R$ 1.000,00") == 1000.0


def test_money_parser_parses_credit_marker_as_positive():
    assert money_parser("2.200,00| C") == 2200.0


def test_money_parser_parses_debit_marker_as_negative():
    assert money_parser("0,60| D") == -0.6


@pytest.mark.parametrize("empty_value", ["", " ", "-"])
def test_money_parser_returns_none_for_blank_or_dash(empty_value):
    assert money_parser(empty_value) is None


def test_money_parser_rejects_invalid_money():
    with pytest.raises(ValueError):
        money_parser("POS")


def test_percent_parser_parses_percent_value():
    assert percent_parser("110.50 %") == 110.5


def test_percent_parser_returns_none_for_blank_or_dash():
    assert percent_parser("-") is None


def test_percent_parser_rejects_invalid_percent():
    with pytest.raises(ValueError):
        percent_parser("abc")


def test_cpf_parser_normalizes_punctuation():
    assert cpf_parser("123.456.789-09") == "12345678909"


def test_cpf_parser_rejects_invalid_length():
    with pytest.raises(ValueError):
        cpf_parser("123")
