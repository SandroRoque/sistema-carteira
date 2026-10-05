import fitz

from key_value_finders import find_sbs_key_value_pairs, find_vertical_key_value_pairs
from models import Movimentacao, NotaCorretagem
from movimentacoes_table_finder import extract_table_movimentacoes
from parsers import br_number_parser, br_date_parser, cpf_parser, money_parser


def extract(page: fitz.Page) -> NotaCorretagem:
    return _extract_nota_corretagem(page)


def _extract_nota_corretagem(page: fitz.Page) -> NotaCorretagem:
    kv_topo = find_vertical_key_value_pairs(
        page,
        ["Folha", "Nr Nota", "Data pregão"],
        y_tol=2.0,
        value_height_ratio=1.0,
    )

    kv_cliente = find_vertical_key_value_pairs(
        page,
        ["C.P.F / C.N.P.J / C.V.M / C.O.B", "Código Cliente", "Assessor"],
        y_tol=2.0,
        value_height_ratio=1.0,
    )

    kv_negocios = find_sbs_key_value_pairs(
        page,
        [
            "Debêntures",
            "Vendas à vista",
            "Compras à vista",
            "Opções - Compras",
            "Opções - vendas",
            "Operações a termo",
            "Valor das oper. c/ títulos públ. (v. nom.)",
            "Valor das operações",
        ],
        x_tolerance=3,
        joiner=" ",
        gap_tolerance_ratio=0.5,
    )

    kv_financeiro = find_sbs_key_value_pairs(
        page,
        [
            "Valor Líquido das operações",
            "Taxa de liquidação",
            "Taxa de registro",
            "Total CBLC",
            "Taxa de termo/opções",
            "Taxa A.N.A.",
            "Emolumentos",
            "Total Bovespa / Soma",
            "Taxa de transferência de Ativos",
            "Clearing",
            "Execução",
            "Execução casa",
            "ISS ( SÃO PAULO )",
            "I.R.R.F s/ operações base",
            "Pis Cofins",
            "Outras",
            "Total Corretagem / Despesas",
            "Líquido para",
        ],
        x_tolerance=3,
        joiner=" ",
        gap_tolerance_ratio=0.5,
    )

    rows = extract_table_movimentacoes(
        page,
        header_text="Q Negociação",
        table_anchor="Resumo de Negócios",
        expected_headers=[
            "Q Negociação", "C/V", "Tipo Mercado", "Prazo",
            "Especificação do título", "Obs.(*)",
            "Quantidade", "Preço / Ajuste", "Valor Operação / Ajuste", "D/C",
        ],
        y_tolerance_ratio=0.5,
        row_y_tol=1.0,
    )

    movimentacoes = [
        Movimentacao(
            mercado=row.get("Q Negociação", ""),
            compra_venda=row.get("C/V", ""),
            tipo_de_mercado=row.get("Tipo Mercado", ""),
            especificacao_do_titulo=row.get("Especificação do título", ""),
            observacao=row.get("Obs.(*)", "") or None,
            quantidade=br_number_parser(row.get("Quantidade", "")),
            preco_ajuste=money_parser(row.get("Preço / Ajuste", "")),
            valor_ajuste=money_parser(row.get("Valor Operação / Ajuste", "")),
            debito_credito=row.get("D/C", ""),
            prazo=row.get("Prazo", "") or None,
        )
        for row in rows
    ]

    return NotaCorretagem(
        corretora_id="safra",
        numero_da_nota=kv_topo["Nr Nota"],
        folha=kv_topo["Folha"] or None,
        data_pregao=br_date_parser(kv_topo["Data pregão"]),
        cpf_cliente=cpf_parser(kv_cliente["C.P.F / C.N.P.J / C.V.M / C.O.B"]),
        codigo_cliente=kv_cliente["Código Cliente"],
        assessor=kv_cliente["Assessor"] or None,
        debentures=money_parser(kv_negocios["Debêntures"]),
        vendas_a_vista=money_parser(kv_negocios["Vendas à vista"]),
        compras_a_vista=money_parser(kv_negocios["Compras à vista"]),
        opcoes_compras=money_parser(kv_negocios["Opções - Compras"]),
        opcoes_vendas=money_parser(kv_negocios["Opções - vendas"]),
        operacoes_a_termo=money_parser(kv_negocios["Operações a termo"]),
        valor_das_operacoes_com_titulos_publicos=money_parser(kv_negocios["Valor das oper. c/ títulos públ. (v. nom.)"]),
        valor_das_operacoes=money_parser(kv_negocios["Valor das operações"]),
        valor_liquido_das_operacoes=money_parser(kv_financeiro["Valor Líquido das operações"]),
        taxa_de_liquidacao=money_parser(kv_financeiro["Taxa de liquidação"]),
        taxa_de_registro=money_parser(kv_financeiro["Taxa de registro"]),
        total_clearing_cblc=money_parser(kv_financeiro["Total CBLC"]),
        taxa_de_termo_opcoes=money_parser(kv_financeiro["Taxa de termo/opções"]),
        taxa_a_n_a=money_parser(kv_financeiro["Taxa A.N.A."]),
        emolumentos=money_parser(kv_financeiro["Emolumentos"]),
        total_bolsa=money_parser(kv_financeiro["Total Bovespa / Soma"]),
        taxa_de_transferencia_de_ativos=money_parser(kv_financeiro["Taxa de transferência de Ativos"]),
        corretagem=money_parser(kv_financeiro["Clearing"]),
        execucao=money_parser(kv_financeiro["Execução"]),
        execucao_casa=money_parser(kv_financeiro["Execução casa"]),
        iss=money_parser(kv_financeiro["ISS ( SÃO PAULO )"]),
        irrf_sobre_operacoes_base_0_00=money_parser(kv_financeiro["I.R.R.F s/ operações base"]),
        pis_cofins=money_parser(kv_financeiro["Pis Cofins"]),
        outras=money_parser(kv_financeiro["Outras"]),
        total_corretagem_despesas=money_parser(kv_financeiro["Total Corretagem / Despesas"]),
        liquido_para=money_parser(kv_financeiro["Líquido para"]),
        movimentacoes=movimentacoes,
    )
