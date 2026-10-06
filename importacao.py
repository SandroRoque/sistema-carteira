"""Import uploaded documents: a job queue in the `uploads` table plus a worker.

Flow
----
1. The web request validates the file and stores it in `uploads` with status
   'pendente' (`registrar`), then wakes the worker.
2. The worker (`Trabalhador`, a thread inside the web process) claims pending
   rows one at a time (`processar_pendentes`), parses the file in an isolated
   child process (isolamento.py) and loads the result in one transaction.
3. Success or failure, the file bytes are dropped (`conteudo` = NULL): only
   the name, the outcome and a SHA-256 of the content remain.

There is no timer polling the database: the worker runs at startup and when
an upload arrives, so an idle app lets a serverless Postgres scale to zero.
The one exception is a job left 'processando' by a crash, which is retried
once it is considered stuck (`TRAVADO_APOS`).

Several worker threads or processes may run at once: jobs are claimed with
FOR UPDATE SKIP LOCKED, and both loaders are idempotent anyway.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import Connection

import formato
from database import connect_sistema, execute, fetch_one, scalar
from isolamento import FalhaIsolada, executar_isolado

logger = logging.getLogger(__name__)

TAMANHO_MAXIMO = 5 * 1024 * 1024
MAX_PENDENTES_POR_USUARIO = 50
MAX_TENTATIVAS = 3
# A job 'processando' for this long was abandoned (the process died).
TRAVADO_APOS = timedelta(minutes=5)

_ASSINATURAS = {
    b"%PDF-": "nota",
    b"PK\x03\x04": "b3",  # .xlsx is a zip archive
}

_MENSAGENS_ERRO = {
    ("nota", "PdfImagemError"): "PDF sem texto (digitalizado como imagem): não é possível ler.",
    ("nota", "LayoutNaoSuportado"): "Este modelo de nota ainda não é lido (o padrão de mercado, com "
                                    "“Nr. Nota” no topo). Se a corretora oferece outro modelo, envie esse.",
    ("b3", "RelatorioB3Invalido"): "Não parece um relatório de movimentação da B3.",
    ("nota", None): "Não foi possível ler esta nota: corretora ou layout não suportado, "
                    "ou dados inconsistentes (ex.: CPF inválido).",
    ("b3", None): "Não foi possível ler a planilha.",
}
_ERRO_TEMPO = "O arquivo demorou demais para ser lido."
_ERRO_RECURSOS = "O arquivo não pôde ser lido (corrompido ou grande demais)."
_ERRO_GRAVACAO = "Não foi possível importar os dados deste arquivo."
_ERRO_TENTATIVAS = "O processamento falhou repetidamente."


class ArquivoRecusado(ValueError):
    """The upload is rejected before queueing; the message is shown to the user."""


# ---------------------------------------------------------------------------
# Enqueueing (web request, tenant-scoped connection)
# ---------------------------------------------------------------------------


def identificar_tipo(conteudo: bytes) -> str:
    for assinatura, tipo in _ASSINATURAS.items():
        if conteudo.startswith(assinatura):
            return tipo
    raise ArquivoRecusado("Formato não suportado: envie notas em PDF ou relatórios da B3 em .xlsx.")


def nome_seguro(nome: str | None) -> str:
    """Base name without control characters, at most 200 characters."""
    nome = re.split(r"[\\/]", nome or "")[-1]
    nome = re.sub(r"[\x00-\x1f\x7f]", "", nome).strip()
    return nome[:200] or "arquivo"


def registrar(
    conn: Connection, usuario_id: int, nome: str, conteudo: bytes, carteira_b3: int | None
) -> str:
    """Queue one uploaded file and return its status ('pendente' or 'duplicado').

    `conn` must be tenant-scoped (database.connect): row-level security then
    guarantees that `carteira_b3` is one of the account's own portfolios.
    Raises ArquivoRecusado for files that are not queued at all.
    """
    if not conteudo:
        raise ArquivoRecusado("Arquivo vazio.")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ArquivoRecusado(f"Arquivo maior que {TAMANHO_MAXIMO // (1024 * 1024)} MB.")
    tipo = identificar_tipo(conteudo)

    investidor_id = None
    if tipo == "b3":
        if carteira_b3 is None or not fetch_one(
            conn, "SELECT 1 FROM investidores WHERE id = :id", id=carteira_b3
        ):
            raise ArquivoRecusado("Escolha a carteira para o relatório da B3.")
        investidor_id = carteira_b3

    pendentes = scalar(
        conn,
        "SELECT COUNT(*) FROM uploads WHERE usuario_id = :u AND status IN ('pendente', 'processando')",
        u=usuario_id,
    )
    if pendentes >= MAX_PENDENTES_POR_USUARIO:
        raise ArquivoRecusado("Muitos arquivos aguardando processamento; tente de novo em instantes.")

    sha256 = hashlib.sha256(conteudo).hexdigest()
    # The same content sent again (to the same portfolio, for B3 reports) is
    # recorded but not processed. A file that failed before is tried again.
    anterior = fetch_one(
        conn,
        """
        SELECT criado_em FROM uploads
        WHERE usuario_id = :u AND sha256 = :sha256 AND status <> 'erro'
          AND investidor_id IS NOT DISTINCT FROM CAST(:investidor_id AS bigint)
        ORDER BY id LIMIT 1
        """,
        u=usuario_id,
        sha256=sha256,
        investidor_id=investidor_id,
    )
    if anterior:
        status, mensagem, conteudo_guardado = (
            "duplicado",
            f"Arquivo idêntico já enviado em {formato.data(anterior['criado_em'].astimezone(formato.FUSO))}.",
            None,
        )
    else:
        status, mensagem, conteudo_guardado = "pendente", None, conteudo

    execute(
        conn,
        """
        INSERT INTO uploads
            (usuario_id, investidor_id, tipo, nome_arquivo, sha256, conteudo, status, mensagem,
             concluido_em)
        VALUES
            (:u, :investidor_id, :tipo, :nome, :sha256, :conteudo, :status, :mensagem,
             CASE WHEN :status = 'pendente' THEN NULL ELSE now() END)
        """,
        u=usuario_id,
        investidor_id=investidor_id,
        tipo=tipo,
        nome=nome_seguro(nome),
        sha256=sha256,
        conteudo=conteudo_guardado,
        status=status,
        mensagem=mensagem,
    )
    return status


# ---------------------------------------------------------------------------
# Parsing (runs in the isolated child process)
# ---------------------------------------------------------------------------


def ler_nota(conteudo: bytes):
    """Every nota in the PDF, transformed (brokers bundle several days)."""
    from extrai_nota_de_negociacao import extrair_notas
    from transformer import transformar

    return [transformar(n) for n in extrair_notas(conteudo)]


def ler_relatorio_b3(conteudo: bytes):
    from carrega_b3 import ler_relatorio

    return ler_relatorio(conteudo)


_LEITORES = {"nota": ler_nota, "b3": ler_relatorio_b3}


# ---------------------------------------------------------------------------
# Processing (background job, owner connection)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Trabalho:
    id: int
    usuario_id: int
    investidor_id: int | None
    tipo: str
    nome_arquivo: str
    conteudo: bytes | None
    tentativas: int


Executor = Callable[..., Any]


def processar_pendentes(executar: Executor = executar_isolado) -> int:
    """Process queued uploads until none is left. Returns how many were handled.

    `executar(funcao, conteudo)` runs a parser; tests pass a direct call.
    """
    processados = 0
    while (trabalho := _reservar()) is not None:
        _processar(trabalho, executar)
        processados += 1
    return processados


def _reservar() -> _Trabalho | None:
    with connect_sistema() as conn:
        row = fetch_one(
            conn,
            """
            UPDATE uploads
            SET status = 'processando', iniciado_em = now(), tentativas = tentativas + 1
            WHERE id = (
                SELECT id FROM uploads
                WHERE status = 'pendente'
                   OR (status = 'processando' AND iniciado_em < now() - :travado)
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, usuario_id, investidor_id, tipo, nome_arquivo, conteudo, tentativas
            """,
            travado=TRAVADO_APOS,
        )
    return _Trabalho(**row) if row else None


def segundos_ate_retomar() -> float | None:
    """Seconds until a job now 'processando' would count as stuck, or None if
    there is none. Lets the worker come back for jobs abandoned by a crash
    without polling the database when there is nothing in flight."""
    with connect_sistema() as conn:
        iniciado = scalar(
            conn,
            """
            SELECT EXTRACT(EPOCH FROM (MIN(iniciado_em) + :travado - now()))
            FROM uploads WHERE status = 'processando'
            """,
            travado=TRAVADO_APOS,
        )
    return None if iniciado is None else max(float(iniciado), 1.0)


def _processar(t: _Trabalho, executar: Executor) -> None:
    if t.tentativas > MAX_TENTATIVAS or t.conteudo is None:
        _finalizar(t.id, "erro", _ERRO_TENTATIVAS)
        return

    try:
        resultado = executar(_LEITORES[t.tipo], t.conteudo)
    except FalhaIsolada as falha:
        logger.warning("upload %s: leitura falhou (%s)", t.id, falha.motivo)
        _finalizar(t.id, "erro", _mensagem_de_falha(t.tipo, falha.motivo))
        return

    try:
        with connect_sistema() as conn:
            if t.tipo == "nota":
                status, mensagem, investidor_id = _gravar_nota(conn, t, resultado)
            else:
                status, mensagem, investidor_id = _gravar_b3(conn, t, resultado)
            _finalizar_em(conn, t.id, status, mensagem, investidor_id)
    except Exception as exc:
        # Only the class name: database errors carry the parameters, which
        # include document data.
        logger.error("upload %s: gravação falhou (%s)", t.id, type(exc).__name__)
        _finalizar(t.id, "erro", _ERRO_GRAVACAO)


def _gravar_nota(conn: Connection, t: _Trabalho, docs) -> tuple[str, str, int]:
    from contas import get_or_create_investidor
    from loader import carregar

    novas = negocios = 0
    investidor_id = None
    for doc in docs:
        if carregar(conn, t.usuario_id, doc, t.nome_arquivo):
            novas += 1
            negocios += len(doc.negociacoes)
        investidor_id = get_or_create_investidor(conn, t.usuario_id, doc.nota.cpf_cliente)
    if not novas:
        return ("duplicado", "Nota já importada anteriormente." if len(docs) == 1
                else f"As {len(docs)} notas do arquivo já tinham sido importadas.", investidor_id)
    mensagem = f"{negocios} negociaç{'ão' if negocios == 1 else 'ões'} importada{'' if negocios == 1 else 's'}"
    if len(docs) > 1:
        mensagem += f" de {novas} nota{'' if novas == 1 else 's'}"
        if novas < len(docs):
            mensagem += f"; {len(docs) - novas} já importada{'' if len(docs) - novas == 1 else 's'}"
    return "concluido", mensagem + ".", investidor_id


def _gravar_b3(conn: Connection, t: _Trabalho, linhas) -> tuple[str, str, int]:
    from carrega_b3 import carregar_linhas

    # Defense in depth: the portfolio was checked at upload, under RLS.
    if not fetch_one(
        conn,
        "SELECT 1 FROM investidores WHERE id = :id AND usuario_id = :u",
        id=t.investidor_id,
        u=t.usuario_id,
    ):
        raise PermissionError("carteira de outra conta")

    inseridas, ignoradas = carregar_linhas(conn, t.investidor_id, linhas, t.nome_arquivo)
    if inseridas == 0:
        return "duplicado", "Todas as datas deste relatório já tinham sido importadas.", t.investidor_id
    mensagem = f"{inseridas} movimentaç{'ão' if inseridas == 1 else 'ões'} importada{'' if inseridas == 1 else 's'}"
    if ignoradas:
        mensagem += f"; {ignoradas} de datas já importadas"
    mensagem += "."
    sem_nota = _sem_nota_no_periodo(conn, t.investidor_id, linhas)
    if sem_nota:
        mensagem += (f" {sem_nota} compra{'' if sem_nota == 1 else 's'} ou venda{'' if sem_nota == 1 else 's'}"
                     f" sem nota de corretagem: veja Pendências.")
    return "concluido", mensagem, t.investidor_id


def _sem_nota_no_periodo(conn: Connection, investidor_id: int, linhas) -> int:
    """B3 settlements in this report's period that no note matches."""
    import cobertura

    datas = [l.data for l in linhas]
    if not datas:
        return 0
    sem_nota, divergentes = cobertura.carregar(conn, investidor_id)
    return sum(min(datas) <= l.data <= max(datas) for l in sem_nota + divergentes)


def _mensagem_de_falha(tipo: str, motivo: str) -> str:
    if motivo == "tempo":
        return _ERRO_TEMPO
    if motivo in ("encerrado", "MemoryError"):
        return _ERRO_RECURSOS
    return _MENSAGENS_ERRO.get((tipo, motivo)) or _MENSAGENS_ERRO[(tipo, None)]


def _finalizar(upload_id: int, status: str, mensagem: str) -> None:
    with connect_sistema() as conn:
        _finalizar_em(conn, upload_id, status, mensagem, None)


def _finalizar_em(
    conn: Connection, upload_id: int, status: str, mensagem: str, investidor_id: int | None
) -> None:
    execute(
        conn,
        """
        UPDATE uploads
        SET status = :status, mensagem = :mensagem, conteudo = NULL, concluido_em = now(),
            investidor_id = COALESCE(CAST(:investidor_id AS bigint), investidor_id)
        WHERE id = :id
        """,
        id=upload_id,
        status=status,
        mensagem=mensagem,
        investidor_id=investidor_id,
    )


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------


class Trabalhador:
    """Runs processar_pendentes in a background thread, on demand."""

    def __init__(self, executar: Executor = executar_isolado):
        self._executar = executar
        self._acordar = threading.Event()
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    def iniciar(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._laco, name="importacao", daemon=True)
            self._thread.start()

    def acordar(self) -> None:
        """New work was queued. Harmless when the thread is not running."""
        self._acordar.set()

    def parar(self, timeout: float = 5) -> None:
        self._parar.set()
        self._acordar.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _laco(self) -> None:
        while not self._parar.is_set():
            # Cleared before draining: an upload arriving meanwhile sets it
            # again, so the wait below returns at once and nothing is missed.
            self._acordar.clear()
            try:
                processar_pendentes(self._executar)
                espera = segundos_ate_retomar()
            except Exception as exc:
                logger.error("worker de importação: %s", type(exc).__name__)
                espera = 30
            self._acordar.wait(espera)


trabalhador = Trabalhador()


def worker_habilitado() -> bool:
    """CARTEIRA_WORKER=false runs the web app without the import worker."""
    return os.environ.get("CARTEIRA_WORKER", "true").lower() not in ("0", "false", "no")
