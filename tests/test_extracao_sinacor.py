"""The market-standard nota layout (XP, Brasil Plural, Safra), on synthetic PDFs.

Every case reproduces the shape of something brokers really print, with
invented values: accents and spacing in labels, D/C letters that are missing
or wrong, CPFs without leading zeros or left blank, cells wrapped over several
lines, two notas under one number, a page printed in landscape.
"""

from decimal import Decimal

import fitz
import pytest

from extrai_nota_de_negociacao import _extrair_pagina, _fundir, agrupar_paginas, extrair_notas

CPF = "12345678909"  # fake, valid check digits
CPF_COM_ZERO = "04719352804"  # fake, valid check digits
XP = ["XP INVESTIMENTOS CORRETORA DE CÂMBIO, TÍTULOS E VALORES MOBILIÁRIOS S.A.", "C.N.P.J: 02.332.886/0001-04"]
BRASIL_PLURAL = ["120-BRASIL PLURAL CCTVM S/A-", "C.N.P.J: 05.816.451/0001-15"]

CABECALHO_XP = [
    (36, "Q"), (44, "Negociação"), (100, "C/V"), (116, "Tipo mercado"), (176, "Prazo"),
    (202, "Especificação do título"), (300, "Obs. (*)"), (336, "Quantidade"),
    (385, "Preço / Ajuste"), (442, "Valor Operação / Ajuste"), (542, "D/C"),
]


def _t(page, x, y, texto, tamanho=6.5):
    page.insert_text((x, y), texto, fontsize=tamanho)


def _pagina(doc, *, corretora=XP, numero="4321", data="10/03/2025", cpf=CPF,
            linhas=(), cabecalho=CABECALHO_XP, resumo=None, financeiro=None, rotulo_cpf="C.P.F./C.N.P.J/C.V.M./C.O.B."):
    """One nota page. `linhas` are (y_offset, [(x, text), ...]) table rows;
    `financeiro` maps a label to (amount, letter or '')."""
    page = doc.new_page(width=595, height=842)
    _t(page, 414, 40, "Nr. nota")
    _t(page, 449, 40, "Folha")
    _t(page, 477, 40, "Data pregão")
    _t(page, 414, 50, numero)
    _t(page, 449, 50, "1")
    _t(page, 477, 50, data)
    for i, linha in enumerate(corretora):
        _t(page, 130, 70 + 9 * i, linha)
    _t(page, 430, 142, rotulo_cpf)
    if cpf:
        _t(page, 430, 151, cpf)
    _t(page, 430, 160, "Código cliente")
    _t(page, 522, 160, "Assessor")
    _t(page, 450, 169, "3-1 0012345")
    _t(page, 532, 169, "00042")
    for x, texto in cabecalho:
        _t(page, x, 240, texto)
    for dy, celulas in linhas:
        for x, texto in celulas:
            _t(page, x, 251 + dy, texto)
    _t(page, 35, 440, "Resumo dos Negócios")
    _t(page, 300, 440, "Resumo Financeiro")
    resumo = resumo or {}
    for i, rotulo in enumerate(["Debêntures", "Vendas à vista", "Compras à vista", "Opções - compras",
                                "Opções - vendas", "Operações à termo", "Valor das operações"]):
        _t(page, 35, 452 + 8 * i, rotulo)
        _t(page, 269, 452 + 8 * i, resumo.get(rotulo, "0,00"))
    for i, (rotulo, (valor, letra)) in enumerate((financeiro or {}).items()):
        _t(page, 300, 452 + 8 * i, rotulo)
        _t(page, 514, 452 + 8 * i, valor)
        if letra:
            _t(page, 548, 452 + 8 * i, letra)
    return page


def _linha(cv, especificacao, quantidade, preco, valor, dc, tipo="VISTA", obs=None, prazo=None, x_espec=196):
    celulas = [(44, "1-BOVESPA"), (101, cv), (116, tipo), (x_espec, especificacao),
               (360, quantidade), (420, preco), (505, valor), (548, dc)]
    if obs:
        celulas.append((310, obs))
    if prazo:
        celulas.append((178, prazo))
    return celulas


def _financeiro_compra(liquido_ops_letra="D", liquido_para_letra="D", taxa_letra="D"):
    # Purchase of 1.000,00 with 0,30 + 0,05 in fees: net 1.000,35 debit.
    return {
        "Valor líquido das operações": ("1.000,00", liquido_ops_letra),
        "Taxa de liquidação": ("0,25", taxa_letra),
        "Taxa de Registro": ("0,05", taxa_letra),
        "Emolumentos": ("0,05", taxa_letra),
        "Total Custos / Despesas": ("0,00", ""),
        "Líquido para 12/03/2025": ("1.000,35", liquido_para_letra),
    }


def _uma(doc):
    return _extrair_pagina(_fundir(doc, list(range(doc.page_count)))[0])


def test_le_compra_da_xp_com_rotulos_acentuados_e_sinal_de_debito():
    doc = fitz.open()
    _pagina(doc, linhas=[(0, _linha("C", "EMPRESA X ON NM", "1000", "1,00", "1.000,00", "D"))],
            resumo={"Compras à vista": "1.000,00", "Valor das operações": "1.000,00"},
            financeiro=_financeiro_compra())
    nota = _uma(doc)

    assert nota.corretora_id == "xp"
    assert nota.numero_da_nota.startswith("4321-20250310-")
    assert (str(nota.data_pregao), nota.cpf_cliente) == ("2025-03-10", CPF)
    [m] = nota.movimentacoes
    # The name starts left of its header and must come whole.
    assert m.especificacao_do_titulo == "EMPRESA X ON NM"
    assert (m.compra_venda, m.tipo_de_mercado, m.quantidade, m.preco_ajuste, m.valor_ajuste) == ("C", "VISTA", 1000, 1.0, 1000.0)
    assert nota.valor_liquido_das_operacoes == -1000.0
    assert nota.liquido_para == Decimal("-1000.35")
    assert nota.taxa_de_liquidacao == Decimal("-0.25")


def test_letras_erradas_nao_invertem_compra():
    # Some XP notas print C beside the net value, the fees and even the final amount of a purchase.
    doc = fitz.open()
    _pagina(doc, linhas=[(0, _linha("C", "EMPRESA X ON NM", "1000", "1,00", "1.000,00", "D"))],
            resumo={"Compras à vista": "1.000,00", "Valor das operações": "1.000,00"},
            financeiro=_financeiro_compra("C", "C", "C"))
    nota = _uma(doc)
    assert nota.valor_liquido_das_operacoes == -1000.0
    assert nota.liquido_para == Decimal("-1000.35")
    assert nota.emolumentos == Decimal("-0.05")


def test_venda_mantem_o_credito():
    doc = fitz.open()
    _pagina(doc, linhas=[(0, _linha("V", "EMPRESA X ON NM", "100", "20,00", "2.000,00", "C"))],
            resumo={"Vendas à vista": "2.000,00", "Valor das operações": "2.000,00"},
            financeiro={"Valor líquido das operações": ("2.000,00", "C"),
                        "Taxa de liquidação": ("0,50", "D"),
                        "Líquido para 12/03/2025": ("1.999,50", "C")})
    nota = _uma(doc)
    assert (nota.valor_liquido_das_operacoes, nota.liquido_para) == (2000.0, 1999.5)


def test_cpf_sem_zeros_a_esquerda_e_cpf_em_branco():
    # A fake CPF starting with zero, printed as a number: the zero is gone.
    doc = fitz.open()
    _pagina(doc, cpf=CPF_COM_ZERO.lstrip("0"), linhas=[(0, _linha("C", "EMPRESA X ON NM", "10", "1,00", "10,00", "D"))],
            financeiro=_financeiro_compra())
    assert _uma(doc).cpf_cliente == CPF_COM_ZERO

    doc = fitz.open()
    _pagina(doc, cpf=None, linhas=[(0, _linha("C", "EMPRESA X ON NM", "10", "1,00", "10,00", "D"))],
            financeiro=_financeiro_compra())
    assert _uma(doc).cpf_cliente is None


def test_cpf_invalido_continua_recusado():
    doc = fitz.open()
    _pagina(doc, cpf="12345678900", linhas=[(0, _linha("C", "EMPRESA X ON NM", "10", "1,00", "10,00", "D"))],
            financeiro=_financeiro_compra())
    with pytest.raises(ValueError, match="check digits"):
        _uma(doc)


def test_tipo_de_mercado_em_branco_e_observacao_numerica():
    linha = [(c if c[0] != 116 else (116, "")) for c in _linha("C", "EMPRESA X ON NM", "100", "10,00", "1.000,00", "D", obs="2")]
    doc = fitz.open()
    _pagina(doc, linhas=[(0, linha)], financeiro=_financeiro_compra())
    [m] = _uma(doc).movimentacoes
    assert m.tipo_de_mercado == ""
    assert m.observacao == "2"
    assert m.quantidade == 100


def test_opcao_com_vencimento_e_codigo():
    doc = fitz.open()
    _pagina(doc, linhas=[(0, _linha("V", "ABCDK350 PN 35,00 ABCD", "1.000", "0,40", "400,00", "C",
                                    tipo="OPCAO DE COMPRA", prazo="11/25"))],
            resumo={"Opções - vendas": "400,00", "Valor das operações": "400,00"},
            financeiro={"Valor líquido das operações": ("400,00", "C"), "Líquido para 12/03/2025": ("399,80", "C")})
    [m] = _uma(doc).movimentacoes
    assert (m.tipo_de_mercado, m.prazo, m.especificacao_do_titulo, m.quantidade) == (
        "OPCAO DE COMPRA", "11/25", "ABCDK350 PN 35,00 ABCD", 1000)


def test_linha_com_valores_fora_do_formato_falha():
    doc = fitz.open()
    _pagina(doc, linhas=[(0, [(44, "1-BOVESPA"), (101, "C"), (196, "EMPRESA X ON NM"), (505, "1.000,00")])],
            financeiro=_financeiro_compra())
    with pytest.raises(ValueError, match="fora do formato"):
        _uma(doc)


def test_celulas_quebradas_em_varias_linhas():
    # Older Brasil Plural options notas: header over three lines with "C/VTipo"
    # fused, market type and security wrapped above and below the amounts.
    cabecalho = [(45, "Q"), (52, "Negociação"), (96, "C/VTipo"), (127, "mercado"), (181, "Prazo"),
                 (266, "título"), (316, "Obs.(*)"), (342, "Quantidade"), (513, "D/C")]
    doc = fitz.open()
    page = _pagina(doc, corretora=BRASIL_PLURAL, cabecalho=cabecalho, rotulo_cpf="C.P.F/C.N.P.J/C.V.M/C.O.B",
                   financeiro={"Valor líquido das operações": ("150,00", "C"), "Liquido para 12/03/2025": ("149,90", "C")},
                   resumo={"Opções - vendas": "150,00"})
    _t(page, 216, 235, "Especificacao")
    _t(page, 253, 245, "do")
    _t(page, 384, 235, "Preço /")
    _t(page, 384, 245, "Ajuste")
    _t(page, 433, 235, "Valor Operação /")
    _t(page, 433, 245, "Ajuste")
    _t(page, 109, 262, "OPCAO DE")
    for x, texto in [(52, "1-BOVESP"), (96, "V"), (181, "11/25"), (203, "ABCDK35"),
                     (371, "500"), (417, "0,30"), (490, "150,00"), (521, "C")]:
        _t(page, x, 267, texto)
    _t(page, 266, 262, "PNA N1")
    _t(page, 109, 271, "COMPRA")
    _t(page, 266, 272, "ABCDE")
    [m] = _uma(doc).movimentacoes
    assert m.tipo_de_mercado == "OPCAO DE COMPRA"
    assert m.especificacao_do_titulo.split() == ["PNA", "N1", "ABCDK35", "ABCDE"]
    assert (m.prazo, m.quantidade, m.valor_ajuste) == ("11/25", 500, 150.0)


def _pdf(*paginas):
    doc = fitz.open()
    for kw in paginas:
        _pagina(doc, **kw)
    return doc.tobytes()


def test_duas_notas_com_o_mesmo_numero_viram_duas():
    # XP issues the stock and the options trades of a day as separate notas under one number.
    acoes = dict(linhas=[(0, _linha("C", "EMPRESA X ON NM", "10", "1,00", "10,00", "D"))],
                 resumo={"Compras à vista": "10,00"}, financeiro=_financeiro_compra())
    opcoes = dict(linhas=[(0, _linha("V", "ABCDK350 PN 35,00 ABCD", "100", "0,10", "10,00", "C", tipo="OPCAO DE COMPRA"))],
                  resumo={"Opções - vendas": "10,00"},
                  financeiro={"Valor líquido das operações": ("10,00", "C"), "Líquido para 12/03/2025": ("9,99", "C")})
    notas = extrair_notas(_pdf(acoes, opcoes))
    assert [len(n.movimentacoes) for n in notas] == [1, 1]
    assert notas[0].liquido_para < 0 < notas[1].liquido_para
    # Same printed number, different notas: different ids, so both are loaded.
    assert notas[0].numero_da_nota != notas[1].numero_da_nota


def test_mesmo_numero_em_outro_dia_e_outra_nota_e_a_mesma_nota_repete_o_id():
    linhas = [(0, _linha("C", "EMPRESA X ON NM", "10", "1,00", "10,00", "D"))]
    um = dict(numero="1", data="10/03/2025", linhas=linhas, resumo={"Compras à vista": "10,00"}, financeiro=_financeiro_compra())
    outro_dia = dict(um, data="11/03/2025")
    [a], [b], [c] = (extrair_notas(_pdf(x)) for x in (um, outro_dia, um))
    assert a.numero_da_nota != b.numero_da_nota
    assert a.numero_da_nota == c.numero_da_nota


def test_nota_de_duas_folhas_continua_uma_so():
    # The first sheet has no final amount yet: the second sheet continues it.
    primeira = dict(linhas=[(0, _linha("C", "EMPRESA X ON NM", "10", "1,00", "10,00", "D"))])
    segunda = dict(linhas=[(0, _linha("C", "EMPRESA Y ON NM", "10", "1,00", "10,00", "D"))],
                   resumo={"Compras à vista": "20,00"}, financeiro=_financeiro_compra())
    doc = fitz.open(stream=_pdf(primeira, segunda), filetype="pdf")
    assert agrupar_paginas(doc) == [[0, 1]]


def test_pagina_impressa_em_paisagem():
    doc = fitz.open()
    _pagina(doc, linhas=[(0, _linha("C", "EMPRESA X ON NM", "10", "1,00", "10,00", "D"))],
            resumo={"Compras à vista": "10,00"}, financeiro=_financeiro_compra())
    # Same content stored sideways and shown upright through /Rotate 90 (a
    # browser printout in landscape): raw text runs bottom to top.
    girado = fitz.open()
    pagina = girado.new_page(width=595, height=842)
    pagina.show_pdf_page(pagina.rect, doc, 0, rotate=90, clip=fitz.Rect(0, 0, 595, 595))
    pagina.set_rotation(90)
    [nota] = extrair_notas(girado.tobytes())
    assert nota.movimentacoes[0].especificacao_do_titulo == "EMPRESA X ON NM"


def test_identidade_nao_muda_com_decimal():
    """Nota ids loaded when parsing produced floats must stay the same."""
    import hashlib
    from datetime import date

    from extractors.sinacor import _identidade
    from models import Movimentacao

    mov = Movimentacao("BOVESPA", "C", "VISTA", "EMPRESA X ON", Decimal("100"), Decimal("12.5"),
                       Decimal("1250.00"), "D")
    esperado = hashlib.sha256(b"C:100.0:1250.0|-1250.4").hexdigest()[:8]

    assert _identidade("7", date(2025, 3, 10), [mov], Decimal("-1250.40")) == f"7-20250310-{esperado}"
