import re
from datetime import date

import pytest
from fastapi.routing import APIRoute

import apuracao
import auth
import demo
import pendencias
from app import conta, importar, paginas
from app.main import app
from database import connect, connect_sistema, fetch_all, scalar
from test_app import _cliente

HOJE = date(2026, 10, 6)


@pytest.fixture
def demo_id(db):
    return demo.recriar(HOJE)


def _investidor(usuario_id: int) -> int:
    with connect(usuario_id) as conn:
        return scalar(conn, "SELECT id FROM investidores")


def test_recriar_monta_a_carteira_ficticia(demo_id):
    investidor_id = _investidor(demo_id)
    with connect(demo_id) as conn:
        meses = apuracao.apuracao(conn, investidor_id)
        tipos = [p.tipo for p in pendencias.listar(conn, investidor_id)]
        pagos = {r["mes"] for r in fetch_all(conn, "SELECT mes FROM darfs_pagos")}

    isentos = [m for m in meses if m.isento and m.vendas_acoes > 0]
    tributados = [m for m in meses if m.darf > 0]
    assert len(isentos) >= 2
    assert pagos == {demo.Gerador(HOJE).mes(demo.VENDA_TRIBUTADA)}
    # The FII sale of last month: DARF still open, due at the end of this month.
    assert [m.mes for m in apuracao.em_aberto(meses)] == [date(2026, 9, 1)]
    assert {m.mes for m in tributados} == pagos | {date(2026, 9, 1)}
    assert meses[-1].prejuizo_comum_saldo > 0
    assert tipos == ["bonificacao"]


def test_nada_e_datado_de_hoje_em_diante(demo_id):
    investidor_id = _investidor(demo_id)
    with connect(demo_id) as conn:
        ultima_negociacao = scalar(conn, "SELECT max(data) FROM negociacoes WHERE investidor_id = :i", i=investidor_id)
        ultima_b3 = scalar(conn, "SELECT max(data) FROM b3_movimentacoes WHERE investidor_id = :i", i=investidor_id)
        avisos = pendencias.avisos(conn, investidor_id, HOJE)
    assert ultima_negociacao < HOJE and ultima_b3 < HOJE
    assert avisos == []


def test_recriar_substitui_a_anterior(demo_id):
    antes = demo.resumo(demo_id)
    novo = demo.recriar(HOJE)
    with connect_sistema() as conn:
        assert scalar(conn, "SELECT count(*) FROM usuarios WHERE demo") == 1
        assert auth.usuario_demo(conn) == novo
    assert demo.resumo(novo) == antes


def test_entrar_na_demo_sem_senha(demo_id):
    c = _cliente()
    assert "Ver demonstração" in c.get("/entrar").text

    resp = c.post("/demo")
    assert resp.status_code == 200
    assert 'id="faixa-demo"' in resp.text
    # The first page already counts the bonus shares without a cost.
    assert "1 pendência" in resp.text
    for pagina in ("/posicoes", "/proventos", "/impostos", "/pendencias", "/importar", "/conta"):
        resp = c.get(pagina)
        assert resp.status_code == 200, pagina
        assert 'id="faixa-demo"' in resp.text


def test_demo_nao_entra_com_senha(demo_id):
    resp = _cliente().post("/entrar", data={"email": demo.EMAIL, "senha": "qualquer-coisa"})
    assert resp.status_code == 400


def _csrf(html: str) -> str:
    return re.search(r'"X-CSRF-Token": "([^"]+)"', html).group(1)


_PUBLICAS = {"/entrar", "/cadastrar", "/demo"}
_LIVRES = {"/sair", "/carteiras/{investidor_id}/selecionar"}


def _escritas():
    # Included routers are nested in app.routes, so list each router's own.
    rotas = [*app.routes, *conta.router.routes, *importar.router.routes, *paginas.router.routes]
    for rota in rotas:
        if isinstance(rota, APIRoute) and rota.path not in _PUBLICAS | _LIVRES:
            for metodo in rota.methods - {"GET", "HEAD", "OPTIONS"}:
                yield metodo, re.sub(r"\{[^}]+\}", "1", rota.path)


def test_demo_nao_altera_nada(demo_id):
    c = _cliente()
    token = _csrf(c.post("/demo").text)
    escritas = list(_escritas())
    assert ("POST", "/importar") in escritas and ("POST", "/conta/excluir") in escritas

    for metodo, caminho in escritas:
        resp = c.request(metodo, caminho, headers={"X-CSRF-Token": token, "HX-Request": "true"})
        assert resp.status_code == 403, (metodo, caminho)
        assert resp.headers.get("X-Somente-Leitura") == "1", (metodo, caminho)

    sem_htmx = c.post("/conta/excluir", data={"csrf_token": token, "senha": "x"})
    assert sem_htmx.status_code == 403
    assert "Somente leitura" in sem_htmx.text
    with connect_sistema() as conn:
        assert auth.usuario_demo(conn) == demo_id


def test_demo_pode_sair(demo_id):
    c = _cliente()
    token = _csrf(c.post("/demo").text)
    resp = c.post("/sair", data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert c.get("/", follow_redirects=False).headers["location"].endswith("/entrar")


def test_sem_demo_nao_ha_entrada(db):
    c = _cliente()
    assert "Ver demonstração" not in c.get("/entrar").text
    assert c.post("/demo").status_code == 404


def test_nova_sessao_apaga_as_expiradas(usuario_id):
    with connect_sistema() as conn:
        auth.criar_sessao(conn, usuario_id)
        conn.exec_driver_sql("UPDATE sessoes SET expira_em = now() - interval '1 minute'")
        auth.criar_sessao(conn, usuario_id)
        assert scalar(conn, "SELECT count(*) FROM sessoes") == 1
