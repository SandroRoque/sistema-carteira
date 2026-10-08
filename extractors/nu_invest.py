import re
from decimal import Decimal

import fitz

from key_value_finders import find_sbs_key_value_pairs, find_vertical_key_value_pairs
from models import Movimentacao, NotaCorretagem, TituloPrivado, TituloPublico
from movimentacoes_table_finder import extract_table_movimentacoes
from parsers import br_number_parser, br_date_parser, cpf_parser, money_parser, percent_parser


_DOC_TYPE_TITULOS_PUBLICOS = "titulos_publicos"
_DOC_TYPE_TITULOS_PRIVADOS = "titulos_privados"
_DOC_TYPE_NOTA_CORRETAGEM = "nota_corretagem"


def extract(page: fitz.Page) -> NotaCorretagem | TituloPublico | TituloPrivado:
    doc_type = _identify_document_type(page)
    if doc_type == _DOC_TYPE_TITULOS_PUBLICOS:
        return _extract_titulos_publicos(page)
    if doc_type == _DOC_TYPE_TITULOS_PRIVADOS:
        return _extract_titulos_privados(page)
    return _extract_nota_corretagem(page)


def _identify_document_type(page: fitz.Page) -> str:
    # "protocolo" is the label for numero_da_nota only in the títulos públicos layout.
    if page.search_for("protocolo"):
        return _DOC_TYPE_TITULOS_PUBLICOS
    # "CNPJ Emissor" only appears in the títulos privados layout.
    if page.search_for("CNPJ Emissor"):
        return _DOC_TYPE_TITULOS_PRIVADOS
    return _DOC_TYPE_NOTA_CORRETAGEM


# The IRRF label carries the base amount ("Base 0,00", "Base R$ 0,00",
# "Base R$ 1.234,56"): match its fixed start.
_IRRF = "I.R.R.F. s/ operações"
_TRANSFERENCIA = "Taxa de Transferência de Ativos"


def _extract_nota_corretagem(page: fitz.Page) -> NotaCorretagem:
    if page.search_for("Nr. Nota") and not page.search_for("Número da nota"):
        # Nu's "padrão de mercado" download: the Sinacor layout other brokers use.
        from extractors import sinacor

        return sinacor.extrair(page, "nu_invest")
    tem_transferencia = bool(page.search_for(_TRANSFERENCIA))
    kv_dados = find_vertical_key_value_pairs(
        page,
        [
            "Folha",
            "Número da nota",
            "Data Pregão",
            "Nome do Cliente",
            "CPF",
            "Código do Cliente",
            "Endereço",
            "Cidade",
            "UF",
            "CEP",
        ],
        y_tol=2.0,
    )

    kv_negocios = find_sbs_key_value_pairs(
        page,
        [
            "Debêntures",
            "Vendas à vista",
            "Compras à vista",
            "Opções - Compras",
            "Opções - Vendas",
            "Operações a Termo",
            "Valor das Operações com Títulos Públicos (V. Nom.)",
        ],
        x_tolerance=3,
        joiner=" ",
    )

    kv_financeiro = find_sbs_key_value_pairs(
        page,
        [
            "Valor Líquido das Operações",
            "Taxa de Liquidação",
            "Taxa de Registro",
            "Total Clearing (CBLC)",
            "Taxa de Termo / Opções",
            "Taxa A.N.A.",
            "Emolumentos",
            "Total Bolsa",
            "Corretagem",
            "ISS (SÃO PAULO)",
            _IRRF,
            "Outras",
            *([_TRANSFERENCIA] if tem_transferencia else []),
            "Total Corretagem/Despesas",
            "Líquido para",
        ],
        x_tolerance=3,
        joiner=" ",
        # "Corretagem" appears as both a section header and a value row label.
        # occurrence_index=0 picks the first match which is the value row in this layout.
        key_options={"Corretagem": {"occurrence_index": 0}},
    )

    rows = extract_table_movimentacoes(
        page,
        header_text="Especificação do Título",
        table_anchor="Resumo dos Negócios",
        expected_headers=[
            "Mercado", "C/V", "Tipo de Mercado",
            "Especificação do Título", "Observação",
            "Quantidade", "Preço/Ajuste", "Valor/Ajuste", "D/C",
        ],
        y_tolerance_ratio=0.5,
        row_y_tol=1.0,
    )

    movimentacoes = [
        Movimentacao(
            mercado=row.get("Mercado", ""),
            compra_venda=row.get("C/V", ""),
            tipo_de_mercado=row.get("Tipo de Mercado", ""),
            especificacao_do_titulo=row.get("Especificação do Título", ""),
            observacao=row.get("Observação", "") or None,
            quantidade=br_number_parser(row.get("Quantidade", "")),
            preco_ajuste=money_parser(row.get("Preço/Ajuste", "")),
            valor_ajuste=money_parser(row.get("Valor/Ajuste", "")),
            debito_credito=row.get("D/C", ""),
        )
        for row in rows
    ]

    return NotaCorretagem(
        corretora_id="nu_invest",
        numero_da_nota=kv_dados["Número da nota"],
        folha=kv_dados["Folha"] or None,
        data_pregao=br_date_parser(kv_dados["Data Pregão"]),
        nome_cliente=kv_dados["Nome do Cliente"] or None,
        cpf_cliente=cpf_parser(kv_dados["CPF"]),
        codigo_cliente=kv_dados["Código do Cliente"],
        endereco=kv_dados["Endereço"] or None,
        cidade=kv_dados["Cidade"] or None,
        uf=kv_dados["UF"] or None,
        cep=kv_dados["CEP"] or None,
        debentures=money_parser(kv_negocios["Debêntures"]),
        vendas_a_vista=money_parser(kv_negocios["Vendas à vista"]),
        compras_a_vista=money_parser(kv_negocios["Compras à vista"]),
        opcoes_compras=money_parser(kv_negocios["Opções - Compras"]),
        opcoes_vendas=money_parser(kv_negocios["Opções - Vendas"]),
        operacoes_a_termo=money_parser(kv_negocios["Operações a Termo"]),
        valor_das_operacoes_com_titulos_publicos=money_parser(kv_negocios["Valor das Operações com Títulos Públicos (V. Nom.)"]),
        valor_liquido_das_operacoes=money_parser(kv_financeiro["Valor Líquido das Operações"]),
        taxa_de_liquidacao=money_parser(kv_financeiro["Taxa de Liquidação"]),
        taxa_de_registro=money_parser(kv_financeiro["Taxa de Registro"]),
        total_clearing_cblc=money_parser(kv_financeiro["Total Clearing (CBLC)"]),
        taxa_de_termo_opcoes=money_parser(kv_financeiro["Taxa de Termo / Opções"]),
        taxa_a_n_a=money_parser(kv_financeiro["Taxa A.N.A."]),
        emolumentos=money_parser(kv_financeiro["Emolumentos"]),
        total_bolsa=money_parser(kv_financeiro["Total Bolsa"]),
        corretagem=money_parser(kv_financeiro["Corretagem"]),
        iss=money_parser(kv_financeiro["ISS (SÃO PAULO)"]),
        irrf_sobre_operacoes_base_0_00=_irrf(kv_financeiro[_IRRF]),
        taxa_de_transferencia_de_ativos=(
            money_parser(kv_financeiro[_TRANSFERENCIA]) if tem_transferencia else None
        ),
        outras=money_parser(kv_financeiro["Outras"]),
        total_corretagem_despesas=money_parser(kv_financeiro["Total Corretagem/Despesas"]),
        liquido_para=money_parser(kv_financeiro["Líquido para"]),
        movimentacoes=movimentacoes,
    )


def _irrf(texto: str) -> Decimal | None:
    """The IRRF amount; the value may come joined with the rest of the label
    ("Base R$ 0,00 R$ 0,00"), so take the last amount."""
    valores = re.findall(r"-?(?:R\$\s*)?[\d.]+,\d{2}", texto)
    return money_parser(valores[-1]) if valores else money_parser(texto)


def _extract_titulos_publicos(page: fitz.Page) -> TituloPublico:
    kv_contraparte = find_vertical_key_value_pairs(
        page,
        ["protocolo", "Contra Parte", "CPF Contra Parte", "Código Contra Parte"],
        y_tol=2.0,
    )

    kv_operacao = find_vertical_key_value_pairs(
        page,
        ["Mercado", "Status", "Tipo", "Data"],
        y_tol=2.0,
    )

    kv_valores = find_vertical_key_value_pairs(
        page,
        ["Título", "Valor 1 título", "Quantidade", "Tx BVMF", "Tx Agente Custódia", "Valor Total"],
        y_tol=2.0,
        key_options={"Título": {"occurrence_index": 2}},
    )

    return TituloPublico(
        corretora_id="nu_invest",
        numero_da_nota=kv_contraparte["protocolo"],
        nome_cliente=kv_contraparte["Contra Parte"],
        cpf_cliente=cpf_parser(kv_contraparte["CPF Contra Parte"]),
        codigo_cliente=kv_contraparte["Código Contra Parte"],
        mercado=kv_operacao["Mercado"],
        status=kv_operacao["Status"],
        tipo=kv_operacao["Tipo"],
        data_de_operacao=br_date_parser(kv_operacao["Data"]),
        titulo=kv_valores["Título"],
        valor_1_titulo=money_parser(kv_valores["Valor 1 título"]),
        quantidade=br_number_parser(kv_valores["Quantidade"]),
        tx_bvmf=money_parser(kv_valores["Tx BVMF"]),
        tx_agente_custodia=percent_parser(kv_valores["Tx Agente Custódia"]),
        valor_total=money_parser(kv_valores["Valor Total"]),
    )


def _extract_titulos_privados(page: fitz.Page) -> TituloPrivado:
    kv_cabecalho = find_vertical_key_value_pairs(
        page,
        ["Número", "Contra Parte", "CPF Contra Parte", "Código Contra Parte"],
        y_tol=2.0,
    )

    kv_negocio = find_vertical_key_value_pairs(
        page,
        ["Nota de", "Local", "Emissor"],
        y_tol=2.0,
        key_options={"Nota de": {"occurrence_index": 1}},
    )

    kv_operacao = find_vertical_key_value_pairs(
        page,
        ["Comando", "Data de Operação", "Data de Liquidação", "CNPJ Emissor"],
        y_tol=2.0,
    )

    kv_titulo = find_vertical_key_value_pairs(
        page,
        ["Conta bancária", "Tipo/Emitente", "Tx. / CUPON %", "Indexador"],
        y_tol=2.0,
    )

    kv_titulo_detalhe = find_vertical_key_value_pairs(
        page,
        ["Título"],
        y_tol=2.0,
        key_options={"Título": {"occurrence_index": 2}},
        multiline_values=True,
        horizontal_gap_ratio=1.5,
        line_y_tolerance_ratio=0.6,
        continuation_gap_ratio=0.9,
        line_joiner=" ",
    )

    kv_prazo = find_vertical_key_value_pairs(
        page,
        ["% do Indexador", "Prazo", "Custódia", "Emissão", "Vencimento"],
        y_tol=2.0,
    )

    kv_financeiro = find_vertical_key_value_pairs(
        page,
        ["Quantidade/Valor nominal", "Preço Unitário da Operação", "Valor da Operação", "Rendimentos"],
        y_tol=2.0,
    )

    kv_fechamento = find_vertical_key_value_pairs(
        page,
        ["Imposto de Renda Federal", "Outros", "IOF", "Valor Líquido", "Especificação/Observação"],
        y_tol=2.0,
    )

    emissao_raw = kv_prazo["Emissão"]
    vencimento_raw = kv_prazo["Vencimento"]

    return TituloPrivado(
        corretora_id="nu_invest",
        numero_da_nota=kv_cabecalho["Número"],
        nome_cliente=kv_cabecalho["Contra Parte"],
        cpf_cliente=cpf_parser(kv_cabecalho["CPF Contra Parte"]),
        codigo_cliente=kv_cabecalho["Código Contra Parte"],
        nota_de=kv_negocio["Nota de"],
        local=kv_negocio["Local"] or None,
        emissor=kv_negocio["Emissor"],
        comando=kv_operacao["Comando"] or None,
        data_de_operacao=br_date_parser(kv_operacao["Data de Operação"]),
        data_de_liquidacao=br_date_parser(kv_operacao["Data de Liquidação"]),
        cnpj_emissor=kv_operacao["CNPJ Emissor"],
        conta_bancaria=kv_titulo["Conta bancária"] or None,
        tipo_emitente=kv_titulo["Tipo/Emitente"] or None,
        taxa_cupom_percentual=percent_parser(kv_titulo["Tx. / CUPON %"]),
        indexador=kv_titulo["Indexador"],
        titulo=kv_titulo_detalhe["Título"],
        percentual_do_indexador=percent_parser(kv_prazo["% do Indexador"]),
        prazo=kv_prazo["Prazo"] or None,
        custodia=kv_prazo["Custódia"] or None,
        emissao=br_date_parser(emissao_raw) if emissao_raw else None,
        vencimento=br_date_parser(vencimento_raw) if vencimento_raw else None,
        quantidade_valor_nominal=br_number_parser(kv_financeiro["Quantidade/Valor nominal"]),
        preco_unitario_da_operacao=money_parser(kv_financeiro["Preço Unitário da Operação"]),
        valor_da_operacao=money_parser(kv_financeiro["Valor da Operação"]),
        rendimentos=kv_financeiro["Rendimentos"] or None,
        imposto_de_renda_federal=money_parser(kv_fechamento["Imposto de Renda Federal"]),
        outras=money_parser(kv_fechamento["Outros"]),
        iof=money_parser(kv_fechamento["IOF"]),
        valor_liquido=money_parser(kv_fechamento["Valor Líquido"]),
        especificacao_observacao=kv_fechamento["Especificação/Observação"] or None,
    )
