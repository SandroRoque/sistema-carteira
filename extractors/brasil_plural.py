import fitz

from key_value_finders import find_sbs_key_value_pairs, find_vertical_key_value_pairs
from models import Movimentacao, NotaCorretagem
from movimentacoes_table_finder import extract_table_movimentacoes
from parsers import br_date_parser, br_number_parser, cpf_parser, money_parser


def extract(page: fitz.Page) -> NotaCorretagem:
    return _extract_nota_corretagem(page)


def _extract_nota_corretagem(page: fitz.Page) -> NotaCorretagem:
    kv_topo = find_vertical_key_value_pairs(
        page,
        ["Nr.Nota", "Folha", "Data pregão"],
        y_tol=2.0,
    )

    kv_cliente = find_vertical_key_value_pairs(
        page,
        ["C.P.F/C.N.P.J/C.V.M/C.O.B", "Código cliente", "Assessor"],
        y_tol=2.0,
        value_height_ratio=1.0,
    )

    kv_negocios = find_sbs_key_value_pairs(
        page,
        [
            "Debêntures",
            "Vendas a vista",
            "Compras a vista",
            "Opções - compras",
            "Opções - vendas",
            "Operações à termo",
            "Valor das oper. c/ títulos públ. (v. nom)",
            "Valor das operações",
        ],
        x_tolerance=3,
        joiner=" ",
        gap_tolerance_ratio=0.5,
    )

    kv_financeiro = find_sbs_key_value_pairs(
        page,
        [
            "Valor líquido das operações",
            "Taxa de liquidação",
            "Taxa de Registro",
            "Total CBLC",
            "Taxa de termo/opções",
            "Taxa A.N.A",
            "Emolumentos",
            "Total Bovespa / Soma",
            "Corretagem",
            "ISS ( São Paulo )",
            "Outras",
            "Total corretagem / Despesas",
            "Liquido para",
        ],
        x_tolerance=3,
        joiner=" ",
        gap_tolerance_ratio=0.5,
        # "Corretagem" appears twice on the page (once as a header label, once as a value row).
        # occurrence_index=1 selects the value row.
        key_options={"Corretagem": {"occurrence_index": 1}},
    )

    rows = extract_table_movimentacoes(
        page,
        header_text="Q Negociação",
        table_anchor="Resumo dos Negócios",
        expected_headers=[
            "Q Negociação", "C/V", "Tipo mercado", "Prazo",
            "Especificacao do título", "Obs.(*)",
            "Quantidade", "Preço / Ajuste", "Valor Operação / Ajuste", "D/C",
        ],
        y_tolerance_ratio=0.5,
        row_y_tol=10.0,
        column_margin_overrides={
            "Especificacao do título": {"right": 30.0},
            "Preço / Ajuste": {"right": 16.0},
        },
    )

    movimentacoes = [
        Movimentacao(
            mercado=row.get("Q Negociação", ""),
            compra_venda=row.get("C/V", ""),
            tipo_de_mercado=row.get("Tipo mercado", ""),
            especificacao_do_titulo=row.get("Especificacao do título", ""),
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
        corretora_id="brasil_plural",
        numero_da_nota=kv_topo["Nr.Nota"],
        folha=kv_topo["Folha"] or None,
        data_pregao=br_date_parser(kv_topo["Data pregão"]),
        cpf_cliente=cpf_parser(kv_cliente["C.P.F/C.N.P.J/C.V.M/C.O.B"]),
        codigo_cliente=kv_cliente["Código cliente"],
        assessor=kv_cliente["Assessor"] or None,
        debentures=money_parser(kv_negocios["Debêntures"]),
        vendas_a_vista=money_parser(kv_negocios["Vendas a vista"]),
        compras_a_vista=money_parser(kv_negocios["Compras a vista"]),
        opcoes_compras=money_parser(kv_negocios["Opções - compras"]),
        opcoes_vendas=money_parser(kv_negocios["Opções - vendas"]),
        operacoes_a_termo=money_parser(kv_negocios["Operações à termo"]),
        valor_das_operacoes_com_titulos_publicos=money_parser(kv_negocios["Valor das oper. c/ títulos públ. (v. nom)"]),
        valor_das_operacoes=money_parser(kv_negocios["Valor das operações"]),
        valor_liquido_das_operacoes=money_parser(kv_financeiro["Valor líquido das operações"]),
        taxa_de_liquidacao=money_parser(kv_financeiro["Taxa de liquidação"]),
        taxa_de_registro=money_parser(kv_financeiro["Taxa de Registro"]),
        total_clearing_cblc=money_parser(kv_financeiro["Total CBLC"]),
        taxa_de_termo_opcoes=money_parser(kv_financeiro["Taxa de termo/opções"]),
        taxa_a_n_a=money_parser(kv_financeiro["Taxa A.N.A"]),
        emolumentos=money_parser(kv_financeiro["Emolumentos"]),
        total_bolsa=money_parser(kv_financeiro["Total Bovespa / Soma"]),
        corretagem=money_parser(kv_financeiro["Corretagem"]),
        iss=money_parser(kv_financeiro["ISS ( São Paulo )"]),
        outras=money_parser(kv_financeiro["Outras"]),
        total_corretagem_despesas=money_parser(kv_financeiro["Total corretagem / Despesas"]),
        liquido_para=money_parser(kv_financeiro["Liquido para"]),
        movimentacoes=movimentacoes,
    )
