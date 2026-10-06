"""Document coverage: B3 statement periods and notes vs B3 settlements."""

from datetime import date
from decimal import Decimal

import cobertura
from cobertura import Cobertura, Liquidacao, Negocio, Periodo, conciliar, juntar
from conftest import CPF_A
from database import connect_sistema, fetch_all
from fabricas import documento, negociacao
from loader import carregar
from test_pendencias import _b3, _posicao

D = Decimal


def liq(dia, qtd, ativo=1, sentido="entrada", valor=None):
    return Liquidacao(ativo, dia, sentido, D(qtd), None if valor is None else D(valor))


def neg(dia, qtd, ativo=1, sentido="entrada"):
    return Negocio(ativo, dia, sentido, D(qtd))


def test_liquidacao_casa_com_a_nota_somada_do_dia():
    # One order filled in two parts on the note, settled once two days later.
    sem_nota, divergentes = conciliar(
        [liq(date(2024, 3, 13), 6)], [neg(date(2024, 3, 11), 2), neg(date(2024, 3, 11), 4)]
    )
    assert (sem_nota, divergentes) == ([], [])


def test_liquidacao_sem_nota_e_divergente():
    sem_nota, divergentes = conciliar(
        [liq(date(2025, 9, 17), 10, valor="512.40"), liq(date(2025, 9, 17), 5, ativo=2)],
        [neg(date(2025, 9, 15), 3, ativo=2)],
    )
    assert sem_nota == [liq(date(2025, 9, 17), 10, valor="512.40")]
    assert divergentes == [liq(date(2025, 9, 17), 5, ativo=2)]


def test_nota_usada_uma_vez_e_fora_da_janela_nao_casa():
    sem_nota, _ = conciliar(
        [liq(date(2025, 10, 8), 30), liq(date(2025, 10, 10), 30), liq(date(2025, 12, 1), 30)],
        [neg(date(2025, 10, 6), 30), neg(date(2025, 10, 8), 30)],
    )
    assert [l.data for l in sem_nota] == [date(2025, 12, 1)]


def test_data_do_pregao_estimada_pula_fim_de_semana():
    assert liq(date(2025, 9, 22), 1).data_pregao == date(2025, 9, 18)  # Monday → Thursday


def test_periodos_juntam_sobreposicao_e_silencios_curtos():
    assert juntar([
        Periodo(date(2025, 1, 6), date(2025, 12, 29)),
        Periodo(date(2023, 6, 5), date(2025, 6, 1)),
        Periodo(date(2026, 1, 5), date(2026, 3, 1)),
        Periodo(date(2026, 6, 1), date(2026, 7, 1)),
    ]) == [Periodo(date(2023, 6, 5), date(2026, 3, 1)), Periodo(date(2026, 6, 1), date(2026, 7, 1))]


def test_o_que_falta_baixar():
    c = Cobertura(
        periodos=[Periodo(date(2023, 6, 5), date(2026, 3, 1)), Periodo(date(2026, 6, 1), date(2026, 7, 1))],
        primeira_nota=date(2023, 1, 10),
        ultima_nota=date(2026, 8, 20),
        hoje=date(2026, 10, 6),
    )
    assert c.a_baixar == [
        Periodo(date(2023, 1, 10), date(2023, 6, 4)),
        Periodo(date(2026, 3, 2), date(2026, 5, 31)),
        Periodo(date(2026, 7, 2), date(2026, 10, 6)),
    ]
    assert c.notas_depois_do_extrato


def test_nota_que_liquida_dentro_do_extrato_nao_pede_extrato_anterior():
    c = Cobertura([Periodo(date(2023, 6, 5), date(2026, 10, 6))], date(2023, 6, 1), None, date(2026, 10, 6))
    assert c.a_baixar == []


def test_arquivo_guarda_o_periodo_mesmo_com_dias_ja_importados(investidor_a):
    _b3(investidor_a, ("Credito", "10/01/2025", "Rendimento", "HGLG11 - CSHG", "NU", 10, 1.1, 11.0))
    _b3(investidor_a,
        ("Credito", "02/01/2025", "Rendimento", "HGLG11 - CSHG", "NU", 10, 1.1, 11.0),
        ("Credito", "10/01/2025", "Rendimento", "HGLG11 - CSHG", "NU", 10, 1.1, 11.0))
    with connect_sistema() as conn:
        [r] = fetch_all(conn, "SELECT periodo_inicio, periodo_fim FROM b3_arquivos_processados")
    assert (r["periodo_inicio"], r["periodo_fim"]) == (date(2025, 1, 2), date(2025, 1, 10))


def test_compra_so_no_extrato_entra_na_posicao_sem_taxas(usuario_id, investidor_a):
    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("PETR4", "entrada", 10, 30.0, date(2025, 8, 4)), data=date(2025, 8, 4)), "1.pdf")
    _b3(investidor_a,
        ("Credito", "06/08/2025", "Transferência - Liquidação", "PETR4 - PETROBRAS", "NU", 10, 30.0, 300.0),
        ("Credito", "17/09/2025", "Transferência - Liquidação", "PETR4 - PETROBRAS", "NU", 10, 48.0, 480.0))

    pos = _posicao(investidor_a, "PETR4")
    assert (pos["qty"], pos["custo_total"]) == (20, D("780.0"))
    with connect_sistema() as conn:
        c = cobertura.cobertura(conn, investidor_a, hoje=date(2025, 9, 30))
    assert [(l.data, l.quantidade) for l in c.sem_nota] == [(date(2025, 9, 17), 10)]
    assert c.a_baixar == [Periodo(date(2025, 9, 18), date(2025, 9, 30))]


def test_pagina_de_importacao_mostra_cobertura(usuario_id, investidor_a):
    from test_app import _cliente, _login

    with connect_sistema() as conn:
        carregar(conn, usuario_id, documento(
            CPF_A, negociacao("PETR4", "entrada", 10, 30.0, date(2025, 8, 4))), "1.pdf")
    _b3(investidor_a,
        ("Credito", "17/09/2025", "Transferência - Liquidação", "PETR4 - PETROBRAS", "NU", 10, 48.0, 480.0))
    c = _cliente()
    c.csrf = _login(c)

    pagina = c.get("/importar").text
    assert "O que os documentos cobrem" in pagina
    assert "17/09/2025" in pagina and "Compras e vendas na B3 sem nota" in pagina
    assert "1 compra ou venda sem nota" in c.get("/pendencias").text
