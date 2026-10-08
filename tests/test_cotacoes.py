"""Shared market price cache: yfinance is hit at most once per ticker per VALIDADE."""

import pytest

import cotacoes
from cotacoes import buscar_cotacoes
from database import connect, connect_sistema, execute, fetch_one


@pytest.fixture
def downloads(monkeypatch):
    """Replaces yfinance; records each call. Set `.precos` / `.falha` to steer it."""

    class Falso:
        precos: dict[str, float | None] = {}
        falha = False
        chamadas: list[list[str]] = []

        def __call__(self, tickers):
            self.chamadas.append(list(tickers))
            if self.falha:
                raise ConnectionError("sem rede")
            return {t: self.precos.get(t) for t in tickers}

    falso = Falso()
    falso.chamadas = []
    monkeypatch.setattr(cotacoes, "_baixar_yfinance", falso)
    return falso


def _envelhecer(ticker):
    with connect_sistema() as conn:
        execute(
            conn,
            "UPDATE cotacoes SET atualizado_em = now() - :idade WHERE ticker = :t",
            idade=cotacoes.VALIDADE * 2,
            t=ticker,
        )


def test_preco_em_cache_nao_e_baixado_de_novo(db, downloads):
    downloads.precos = {"PETR4": 38.5, "VALE3": 60.0}
    with connect_sistema() as conn:
        assert buscar_cotacoes(conn, ["PETR4", "VALE3"]) == {"PETR4": 38.5, "VALE3": 60.0}
    with connect_sistema() as conn:
        assert buscar_cotacoes(conn, ["PETR4", "VALE3"]) == {"PETR4": 38.5, "VALE3": 60.0}
    assert downloads.chamadas == [["PETR4", "VALE3"]]


def test_baixa_so_os_tickers_que_faltam(db, downloads):
    downloads.precos = {"PETR4": 38.5, "VALE3": 60.0}
    with connect_sistema() as conn:
        buscar_cotacoes(conn, ["PETR4"])
        assert buscar_cotacoes(conn, ["PETR4", "VALE3"]) == {"PETR4": 38.5, "VALE3": 60.0}
    assert downloads.chamadas == [["PETR4"], ["VALE3"]]


def test_preco_vencido_e_atualizado(db, downloads):
    downloads.precos = {"PETR4": 38.5}
    with connect_sistema() as conn:
        buscar_cotacoes(conn, ["PETR4"])
    _envelhecer("PETR4")

    downloads.precos = {"PETR4": 40.0}
    with connect_sistema() as conn:
        assert buscar_cotacoes(conn, ["PETR4"]) == {"PETR4": 40.0}
    assert len(downloads.chamadas) == 2


def test_falha_no_download_devolve_preco_vencido(db, downloads):
    downloads.precos = {"PETR4": 38.5}
    with connect_sistema() as conn:
        buscar_cotacoes(conn, ["PETR4"])
    _envelhecer("PETR4")

    downloads.falha = True
    with connect_sistema() as conn:
        assert buscar_cotacoes(conn, ["PETR4", "VALE3"]) == {"PETR4": 38.5, "VALE3": None}

    # The failure is not cached: the next call tries again.
    downloads.falha = False
    downloads.precos = {"PETR4": 40.0, "VALE3": 60.0}
    with connect_sistema() as conn:
        assert buscar_cotacoes(conn, ["PETR4", "VALE3"]) == {"PETR4": 40.0, "VALE3": 60.0}


def test_ticker_desconhecido_tambem_fica_em_cache(db, downloads):
    with connect_sistema() as conn:
        assert buscar_cotacoes(conn, ["XXXX3"]) == {"XXXX3": None}
        assert buscar_cotacoes(conn, ["XXXX3"]) == {"XXXX3": None}
    assert downloads.chamadas == [["XXXX3"]]


def test_sem_preco_novo_mantem_o_antigo(db, downloads):
    downloads.precos = {"PETR4": 38.5}
    with connect_sistema() as conn:
        buscar_cotacoes(conn, ["PETR4"])
    _envelhecer("PETR4")

    downloads.precos = {}
    with connect_sistema() as conn:
        assert buscar_cotacoes(conn, ["PETR4"]) == {"PETR4": 38.5}
        assert fetch_one(conn, "SELECT preco FROM cotacoes WHERE ticker = 'PETR4'")["preco"] == 38.5


def test_cache_e_compartilhado_entre_contas(db, usuario_id, investidor_b, downloads):
    downloads.precos = {"PETR4": 38.5}
    with connect(usuario_id) as conn:
        assert buscar_cotacoes(conn, ["PETR4"]) == {"PETR4": 38.5}
    # Another account (and the restricted role in general) reads it without a new download.
    with connect(None) as conn:
        assert buscar_cotacoes(conn, ["PETR4"]) == {"PETR4": 38.5}
    assert len(downloads.chamadas) == 1


def test_lista_vazia_nao_consulta_nada(db, downloads):
    with connect_sistema() as conn:
        assert buscar_cotacoes(conn, []) == {}
    assert downloads.chamadas == []
