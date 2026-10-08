"""PDF extraction pieces that do not need a real nota: bundles of several
notas in one PDF, the newer Nu amount format and unsupported layouts."""

from decimal import Decimal

import fitz

from extrai_nota_de_negociacao import agrupar_paginas
from extractors.nu_invest import _irrf
from parsers import money_parser


def _doc(*paginas: str) -> fitz.Document:
    doc = fitz.open()
    for texto in paginas:
        doc.new_page().insert_text((72, 72), texto)
    return doc


def test_valor_com_sinal_antes_do_real():
    assert money_parser("-R$ 1.234,56") == Decimal("-1234.56")
    assert money_parser("R$ 1.234,56") == Decimal("1234.56")
    assert money_parser("1.234,56 D") == Decimal("-1234.56")


def test_irrf_ignora_a_base_no_rotulo():
    assert _irrf("Base R$ 0,00 R$ 0,00") == 0.0
    assert _irrf("Base R$ 2.500,00 R$ 0,13") == Decimal("0.13")
    assert _irrf("0,00") == 0.0


def test_pdf_com_varias_notas_e_dividido_por_numero():
    doc = _doc(
        "Data Pregão\n03/02/2025\nNúmero da nota\n1111111",
        "Data Pregão\n10/02/2025\nNúmero da nota\n2222222",
        "continuação sem cabeçalho",
        "Nr. Nota\nFolha\nData pregão\n3333333\n1\n17/02/2025",
    )
    assert agrupar_paginas(doc) == [[0], [1, 2], [3]]


def test_nota_de_varias_folhas_continua_uma_so():
    doc = _doc("Número da nota\n555\nFolha 1/2", "Número da nota\n555\nFolha 2/2")
    assert agrupar_paginas(doc) == [[0, 1]]
    assert agrupar_paginas(_doc("sem número", "nada")) == [[0, 1]]
