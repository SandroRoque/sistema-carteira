"""Every account accepts the current terms of use before using the app."""

import auth
from database import connect_sistema, execute, fetch_one
from test_app import _cliente, _login


def _sem_aceite(usuario_id):
    with connect_sistema() as conn:
        execute(conn, "UPDATE usuarios SET termos_versao = NULL, termos_aceitos_em = NULL WHERE id = :id",
                id=usuario_id)


def test_termos_sao_publicos():
    pagina = _cliente().get("/termos").text
    assert "A responsabilidade é sua" in pagina and "Aceitar e continuar" not in pagina


def test_conta_sem_aceite_vai_para_os_termos(usuario_id, investidor_a):
    _sem_aceite(usuario_id)
    c = _cliente()
    csrf = _login(c)

    resp = c.get("/posicoes", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/termos"
    assert c.get("/termos").text.count("Aceitar e continuar") == 1
    # LGPD rights stay reachable without accepting.
    assert c.get("/conta").status_code == 200

    assert c.post("/termos", data={"csrf_token": csrf}).status_code == 422
    resp = c.post("/termos", data={"csrf_token": csrf, "aceito": "sim"}, follow_redirects=False)
    assert resp.status_code == 303
    assert c.get("/posicoes").status_code == 200
    with connect_sistema() as conn:
        row = fetch_one(conn, "SELECT termos_versao, termos_aceitos_em FROM usuarios WHERE id = :id", id=usuario_id)
    assert row["termos_versao"] == auth.TERMOS_VERSAO and row["termos_aceitos_em"] is not None


def test_nova_versao_pede_novo_aceite(usuario_id, investidor_a, monkeypatch):
    c = _cliente()
    _login(c)
    assert c.get("/posicoes").status_code == 200
    monkeypatch.setattr(auth, "TERMOS_VERSAO", "2099-01-01")
    assert c.get("/posicoes", follow_redirects=False).headers["location"] == "/termos"
