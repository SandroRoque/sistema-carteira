import io
import zipfile
from datetime import date

import pytest

import especificacoes_b3 as eb3

TABELA = """nome,classe,ticker,primeiro,ultimo
EMPRESA X,ON,EMPX3,2015-01-02,2026-09-30
EMPRESA X,PN,EMPX4,2015-01-02,2026-09-30
LOGIST Y,ON,LOGY3,2015-01-02,2017-04-28
LOGIST Y,ON,LOGI3,2017-05-02,2026-09-30
FUNDO Z,CI,FUNZ11,2019-03-01,2026-09-30
"""


@pytest.fixture
def tabela(tmp_path, monkeypatch):
    arquivo = tmp_path / "especificacoes_b3.csv"
    arquivo.write_text(TABELA)
    monkeypatch.setattr(eb3, "ARQUIVO", arquivo)
    eb3._tabela.cache_clear()
    yield
    eb3._tabela.cache_clear()


def test_nome_e_classe_viram_ticker(tabela):
    assert eb3.resolver("EMPRESA X PN N2") == "EMPX4"
    assert eb3.resolver("EMPRESA X ON EDJ NM", date(2024, 5, 2)) == "EMPX3"
    assert eb3.resolver("FUNDO Z CI") == "FUNZ11"


def test_ticker_que_mudou_depende_da_data(tabela):
    assert eb3.resolver("LOGIST Y ON NM", date(2016, 7, 18)) == "LOGY3"
    assert eb3.resolver("LOGIST Y ON NM", date(2024, 1, 10)) == "LOGI3"


def test_raiz_do_ticker_no_lugar_do_nome(tabela):
    # Some XP notas print "EMPX PN N2" instead of the trading name.
    assert eb3.resolver("EMPX PN N2", date(2025, 6, 30)) == "EMPX4"


def test_exercicio_de_opcao_e_a_acao_subjacente(tabela):
    assert eb3.subjacente("EMPXA375E PN 36,27", date(2024, 1, 19)) == "EMPX4"
    assert eb3.subjacente("EMPXO417W1E PN 41,75") == "EMPX4"
    assert eb3.subjacente("EMPRESA X PN N2") is None


def test_desconhecido_fica_sem_ticker(tabela):
    assert eb3.resolver("OUTRA COISA ON NM") is None
    assert eb3.resolver("SEM CLASSE") is None


def _cotahist(*registros: tuple[str, str, str, str, str]) -> bytes:
    """A COTAHIST zip with fixed-width quote records: (date, ticker, market, name, spec)."""
    linhas = ["00COTAHIST.2016BOVESPA 20170101" + " " * 214]
    for dia, ticker, mercado, nome, especi in registros:
        linha = "01" + dia + "02" + ticker.ljust(12) + mercado + nome.ljust(12) + especi.ljust(10)
        linhas.append(linha.ljust(245))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("COTAHIST_A2016.TXT", "\n".join(linhas).encode("latin-1"))
    return buffer.getvalue()


def test_le_os_registros_do_mercado_a_vista():
    conteudo = _cotahist(
        ("20160718", "EMPX3", "010", "EMPRESA X", "ON      NM"),
        ("20160718", "EMPX3F", "020", "EMPRESA X", "ON      NM"),  # fractional: skipped
        ("20160718", "EMPXH20", "070", "EMPX", "ON"),  # option: skipped
    )
    assert list(eb3._linhas_cotahist(conteudo)) == [(date(2016, 7, 18), "EMPX3", "EMPRESA X", ["ON", "NM"])]


def test_carga_da_nota_resolve_o_nome_para_o_ticker(tabela, usuario_id):
    from conftest import CPF_A
    from database import connect_sistema, fetch_all
    from fabricas import documento, negociacao
    from loader import carregar

    doc = documento(
        CPF_A,
        negociacao("EMPRESA X PN EDJ N2", data=date(2025, 3, 10)),
        negociacao("EMPXA375E PN 36,27", linha=2, data=date(2025, 3, 10), tipo_de_mercado="EXERC OPC COMPRA"),
    )
    with connect_sistema() as conn:
        carregar(conn, usuario_id, doc, "nota.pdf")
        ativos = fetch_all(
            conn,
            "SELECT DISTINCT a.ticker, a.tipo FROM negociacoes n JOIN ativos a ON a.id = n.ativo_id",
        )
    assert [dict(r) for r in ativos] == [{"ticker": "EMPX4", "tipo": "acao"}]
