"""Document import: queueing, isolated parsing, the worker and the upload page."""

import io
import os
import time

import pandas as pd
import pytest
from sqlalchemy.exc import DBAPIError

import importacao
from conftest import CPF_A, CPF_B
from database import connect, connect_sistema, execute, fetch_all, fetch_one, scalar
from fabricas import documento, negociacao
from importacao import ArquivoRecusado, processar_pendentes, registrar
from isolamento import FalhaIsolada, executar_isolado
from test_app import _cliente, _login
from test_carrega_b3 import _COLUNAS

_DIVIDENDO = ("Credito", "15/03/2025", "Dividendo", "PETR4 - PETROBRAS", "NU INVEST", 100, 1.5, 150.0)
_JCP = ("Credito", "16/04/2025", "Juros Sobre Capital Próprio", "PETR4 - PETROBRAS", "NU INVEST", 100, 0.5, 50.0)


def _xlsx(*linhas) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame(list(linhas), columns=_COLUNAS).to_excel(buffer, index=False)
    return buffer.getvalue()


def _pdf(texto: str | None) -> bytes:
    import fitz

    doc = fitz.open()
    pagina = doc.new_page()
    if texto:
        pagina.insert_text((72, 72), texto)
    return doc.tobytes()


def _direto(funcao, conteudo):
    """Executor for tests: parse in-process instead of in a child."""
    return funcao(conteudo)


def _registrar(usuario_id, conteudo, carteira_b3=None, nome="arquivo"):
    with connect(usuario_id) as conn:
        return registrar(conn, usuario_id, nome, conteudo, carteira_b3)


def _upload(upload_id=None):
    with connect_sistema() as conn:
        if upload_id is None:
            upload_id = scalar(conn, "SELECT MAX(id) FROM uploads")
        return fetch_one(conn, "SELECT * FROM uploads WHERE id = :id", id=upload_id)


def _uploads():
    with connect_sistema() as conn:
        return fetch_all(conn, "SELECT * FROM uploads ORDER BY id")


# ---------------------------------------------------------------------------
# Isolation (module-level functions: the spawned child imports them by name)
# ---------------------------------------------------------------------------


def _soma(a, b):
    return a + b


def _falha():
    raise ValueError("pode conter um CPF")


def _demora():
    time.sleep(30)


def _morre():
    os._exit(1)


def _esgota_memoria():
    return bytearray(1024**3)


def test_isolado_devolve_resultado():
    assert executar_isolado(_soma, 2, 3) == 5


@pytest.mark.parametrize("funcao, motivo, kw", [
    (_falha, "ValueError", {}),
    (_demora, "tempo", {"tempo_limite_s": 1}),
    (_morre, "encerrado", {}),
    (_esgota_memoria, "MemoryError", {"memoria_limite": 512 * 1024**2}),
])
def test_isolado_contem_falhas(funcao, motivo, kw):
    with pytest.raises(FalhaIsolada) as exc:
        executar_isolado(funcao, **kw)
    # Only the class name crosses: never the message, which may hold document data.
    assert exc.value.motivo == motivo


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------


def test_registra_pdf_e_xlsx_pelo_conteudo(usuario_id, investidor_a):
    assert _registrar(usuario_id, _pdf("x"), nome="relatorio.xlsx") == "pendente"
    assert _upload()["tipo"] == "nota"
    assert _registrar(usuario_id, _xlsx(_DIVIDENDO), investidor_a, nome="nota.pdf") == "pendente"
    assert _upload()["tipo"] == "b3"


@pytest.mark.parametrize("conteudo, mensagem", [
    (b"", "vazio"),
    (b"GIF89a", "Formato"),
    (b"%PDF-" + b"0" * importacao.TAMANHO_MAXIMO, "maior"),
])
def test_recusa_arquivos_invalidos(usuario_id, conteudo, mensagem):
    with pytest.raises(ArquivoRecusado, match=mensagem):
        _registrar(usuario_id, conteudo)


def test_relatorio_b3_exige_carteira_propria(usuario_id, investidor_a, investidor_b):
    with pytest.raises(ArquivoRecusado, match="carteira"):
        _registrar(usuario_id, _xlsx(_DIVIDENDO))
    # Another account's portfolio is invisible under row-level security.
    with pytest.raises(ArquivoRecusado, match="carteira"):
        _registrar(usuario_id, _xlsx(_DIVIDENDO), investidor_b)


def test_mesmo_conteudo_nao_e_processado_de_novo(usuario_id):
    pdf = _pdf("x")
    assert _registrar(usuario_id, pdf) == "pendente"
    assert _registrar(usuario_id, pdf) == "duplicado"
    assert _upload()["conteudo"] is None


def test_arquivo_que_falhou_pode_ser_reenviado(usuario_id):
    pdf = _pdf("x")
    _registrar(usuario_id, pdf)
    with connect_sistema() as conn:
        execute(conn, "UPDATE uploads SET status = 'erro', conteudo = NULL")

    assert _registrar(usuario_id, pdf) == "pendente"


def test_limite_de_pendentes(usuario_id, monkeypatch):
    monkeypatch.setattr(importacao, "MAX_PENDENTES_POR_USUARIO", 1)
    _registrar(usuario_id, _pdf("1"))
    with pytest.raises(ArquivoRecusado, match="aguardando"):
        _registrar(usuario_id, _pdf("2"))


def test_nome_do_arquivo_e_saneado():
    assert importacao.nome_seguro("C:\\Users\\x\\nota\x00.pdf") == "nota.pdf"
    assert importacao.nome_seguro("../../etc/passwd") == "passwd"
    assert importacao.nome_seguro("") == "arquivo"
    assert len(importacao.nome_seguro("a" * 500)) == 200


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def test_processa_relatorio_b3_isolado(usuario_id, investidor_a):
    _registrar(usuario_id, _xlsx(_DIVIDENDO, _JCP), investidor_a, nome="mov.xlsx")

    assert processar_pendentes() == 1

    upload = _upload()
    assert (upload["status"], upload["mensagem"]) == ("concluido", "2 movimentações importadas.")
    assert upload["conteudo"] is None
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM b3_movimentacoes WHERE investidor_id = :i", i=investidor_a) == 2


def test_relatorio_b3_ja_importado(usuario_id, investidor_a):
    _registrar(usuario_id, _xlsx(_DIVIDENDO), investidor_a)
    _registrar(usuario_id, _xlsx(_DIVIDENDO, _JCP), investidor_a)
    processar_pendentes(_direto)
    assert _upload()["mensagem"] == "1 movimentação importada; 1 de datas já importadas."

    _registrar(usuario_id, _xlsx(_JCP, _DIVIDENDO), investidor_a)
    processar_pendentes(_direto)
    assert _upload()["status"] == "duplicado"


def test_processa_nota_e_descobre_a_carteira(usuario_id):
    doc = documento(CPF_B, negociacao("PETR4"), negociacao("VALE3", linha=2))
    _registrar(usuario_id, _pdf("nota"), nome="nota.pdf")

    processar_pendentes(lambda _funcao, _conteudo: doc)

    upload = _upload()
    assert (upload["status"], upload["mensagem"]) == ("concluido", "2 negociações importadas.")
    with connect_sistema() as conn:
        investidor = fetch_one(conn, "SELECT usuario_id, cpf_mascarado FROM investidores WHERE id = :i",
                               i=upload["investidor_id"])
        assert scalar(conn, "SELECT filename FROM notas") == "nota.pdf"
    assert investidor["usuario_id"] == usuario_id
    assert investidor["cpf_mascarado"] == "***.654.321-**"


def test_nota_ja_importada(usuario_id):
    doc = documento(CPF_A, negociacao("PETR4"))
    _registrar(usuario_id, _pdf("a"))
    _registrar(usuario_id, _pdf("b"))  # different file, same nota

    processar_pendentes(lambda _funcao, _conteudo: doc)

    assert [u["status"] for u in _uploads()] == ["concluido", "duplicado"]


@pytest.mark.parametrize("pdf, mensagem", [
    (_pdf(None), "PDF sem texto"),
    (_pdf("Um documento qualquer, de nenhuma corretora conhecida. " * 3), "Não foi possível ler esta nota"),
])
def test_pdf_ilegivel_vira_erro_sem_conteudo(usuario_id, pdf, mensagem):
    _registrar(usuario_id, pdf)

    processar_pendentes()  # real isolated parsing

    upload = _upload()
    assert upload["status"] == "erro"
    assert upload["mensagem"].startswith(mensagem)
    assert upload["conteudo"] is None


def test_falha_de_tempo_e_recursos(usuario_id):
    _registrar(usuario_id, _pdf("a"))
    _registrar(usuario_id, _pdf("b"))
    motivos = iter(["tempo", "encerrado"])

    def falha(_funcao, _conteudo):
        raise FalhaIsolada(next(motivos))

    processar_pendentes(falha)

    assert [u["mensagem"] for u in _uploads()] == [
        importacao._ERRO_TEMPO, importacao._ERRO_RECURSOS,
    ]


def test_erro_ao_gravar_vira_erro(usuario_id, investidor_a, investidor_b):
    # A B3 upload pointing at another account's portfolio can only be forged
    # past RLS; the worker re-checks ownership and refuses it.
    _registrar(usuario_id, _xlsx(_DIVIDENDO), investidor_a)
    with connect_sistema() as conn:
        execute(conn, "UPDATE uploads SET investidor_id = :b", b=investidor_b)

    processar_pendentes(_direto)

    upload = _upload()
    assert (upload["status"], upload["mensagem"]) == ("erro", importacao._ERRO_GRAVACAO)
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM b3_movimentacoes") == 0


def test_trabalho_travado_e_retomado(usuario_id, investidor_a):
    _registrar(usuario_id, _xlsx(_DIVIDENDO), investidor_a)
    with connect_sistema() as conn:
        execute(conn, "UPDATE uploads SET status = 'processando', iniciado_em = now(), tentativas = 1")

    assert processar_pendentes(_direto) == 0  # still within TRAVADO_APOS
    assert importacao.segundos_ate_retomar() > 60

    with connect_sistema() as conn:
        execute(conn, "UPDATE uploads SET iniciado_em = now() - interval '1 hour'")
    assert processar_pendentes(_direto) == 1
    assert _upload()["status"] == "concluido"
    assert importacao.segundos_ate_retomar() is None


def test_desiste_apos_tentativas(usuario_id):
    _registrar(usuario_id, _pdf("x"))
    with connect_sistema() as conn:
        execute(conn, "UPDATE uploads SET tentativas = :n", n=importacao.MAX_TENTATIVAS)

    processar_pendentes(lambda *_: pytest.fail("não deveria processar"))

    assert (_upload()["status"], _upload()["conteudo"]) == ("erro", None)


def test_conteudo_nao_fica_guardado_apos_processar(usuario_id):
    _registrar(usuario_id, _pdf("x"))
    with connect_sistema() as conn, pytest.raises(DBAPIError):
        with conn.begin_nested():
            execute(conn, "UPDATE uploads SET status = 'concluido'")


def test_trabalhador_processa_ao_ser_acordado(usuario_id, investidor_a):
    trabalhador = importacao.Trabalhador(_direto)
    trabalhador.iniciar()
    try:
        _registrar(usuario_id, _xlsx(_DIVIDENDO), investidor_a)
        trabalhador.acordar()
        for _ in range(100):
            if _upload()["status"] == "concluido":
                break
            time.sleep(0.05)
        assert _upload()["status"] == "concluido"
    finally:
        trabalhador.parar()


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


def test_envios_isolados_por_conta(usuario_id, investidor_b):
    _registrar(usuario_id, _pdf("x"))
    with connect_sistema() as conn:
        bruno = scalar(conn, "SELECT usuario_id FROM investidores WHERE id = :id", id=investidor_b)

    with connect(bruno) as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM uploads") == 0
    with connect(usuario_id) as conn:
        assert scalar(conn, "SELECT COUNT(*) FROM uploads") == 1


def test_papel_web_nao_altera_envios(usuario_id):
    _registrar(usuario_id, _pdf("x"))
    with pytest.raises(DBAPIError, match="permission denied"):
        with connect(usuario_id) as conn:
            execute(conn, "UPDATE uploads SET status = 'concluido', conteudo = NULL")


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


@pytest.fixture
def acordado(monkeypatch):
    chamadas = []
    monkeypatch.setattr(importacao.trabalhador, "acordar", lambda: chamadas.append(1))
    return chamadas


@pytest.fixture
def cliente(investidor_a):
    c = _cliente()
    c.csrf = _login(c)
    return c


def _enviar(cliente, *arquivos, **dados):
    return cliente.post(
        "/importar",
        files=[("arquivos", (nome, conteudo)) for nome, conteudo in arquivos],
        data={"csrf_token": cliente.csrf, **dados},
    )


def test_pagina_de_importacao_sem_carteira(db, usuario_id):
    c = _cliente()
    _login(c)

    resp = c.get("/importar")

    assert resp.status_code == 200
    assert "envie antes ao menos uma nota" in resp.text


def test_envio_enfileira_e_acorda_o_worker(cliente, investidor_a, acordado):
    resp = _enviar(cliente, ("nota.pdf", _pdf("x")), ("mov.xlsx", _xlsx(_DIVIDENDO)),
                   carteira_b3=str(investidor_a))

    assert resp.status_code == 200
    assert acordado == [1]
    assert [u["status"] for u in _uploads()] == ["pendente", "pendente"]
    # The list refreshes itself while something is queued.
    assert 'hx-get="/importar/lista"' in resp.text


def test_envio_recusado_mostra_motivo(cliente, acordado):
    resp = _enviar(cliente, ("foto.gif", b"GIF89a..."))

    assert resp.status_code == 422
    assert "foto.gif" in resp.text and "Formato não suportado" in resp.text
    assert acordado == []


def test_lista_para_de_atualizar_quando_termina(cliente, usuario_id, investidor_a):
    _registrar(usuario_id, _xlsx(_DIVIDENDO), investidor_a, nome="mov.xlsx")
    assert 'hx-get="/importar/lista"' in cliente.get("/importar/lista").text

    processar_pendentes(_direto)

    resp = cliente.get("/importar/lista")
    assert "hx-get" not in resp.text
    assert "1 movimentação importada." in resp.text


def test_envio_exige_csrf(cliente):
    resp = cliente.post("/importar", files=[("arquivos", ("a.pdf", _pdf("x")))])
    assert resp.status_code == 403


def test_corpo_grande_demais_e_recusado_antes_de_ler(cliente):
    resp = cliente.post("/sair", content=b"x" * (1024 * 1024 + 1),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 413


def test_corpo_sem_tamanho_e_recusado(cliente):
    def pedacos():
        yield b"csrf_token=x"

    resp = cliente.post("/sair", content=pedacos(),
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 411


def test_exportacao_lista_envios_sem_o_arquivo(cliente, usuario_id):
    _registrar(usuario_id, _pdf("x"), nome="nota.pdf")

    envios = cliente.get("/conta/exportar").json()["envios"]

    assert [e["nome_arquivo"] for e in envios] == ["nota.pdf"]
    assert "conteudo" not in envios[0]


def test_relatorio_b3_avisa_compras_sem_nota(usuario_id, investidor_a):
    compra = ("Credito", "17/03/2026", "Transferência - Liquidação", "PETR4 - PETROBRAS", "NU", 10, 48.0, 480.0)
    _registrar(usuario_id, _xlsx(_DIVIDENDO, compra), investidor_a)
    processar_pendentes(_direto)

    assert _upload()["mensagem"] == (
        "2 movimentações importadas. 1 compra ou venda sem nota de corretagem: veja Pendências."
    )
