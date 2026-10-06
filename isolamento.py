"""Run untrusted-input parsing in a short-lived child process.

Uploaded PDFs and spreadsheets are parsed by native code (MuPDF, the zip
reader behind openpyxl). A malformed file could crash, hang or exhaust the
memory of the process doing it. The web app runs on a single machine, so a
crash there would take the site down — and, since an interrupted job is
retried, keep taking it down. In a child process the damage is contained:
the parent sees an error and moves on.

The child is started with "spawn" (a fresh interpreter, safe from a
multi-threaded parent) and gets an address-space limit and a wall-clock
deadline. Results and errors come back over a pipe; for an exception only its
class name crosses, since its message may quote document content.
"""

from __future__ import annotations

import multiprocessing
from typing import Any, Callable

TEMPO_LIMITE_S = 60
MEMORIA_LIMITE = 2 * 1024**3


class FalhaIsolada(Exception):
    """The child failed. `motivo` is an exception class name, 'tempo' or 'encerrado'."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def executar_isolado(
    funcao: Callable[..., Any],
    *args: Any,
    tempo_limite_s: float = TEMPO_LIMITE_S,
    memoria_limite: int = MEMORIA_LIMITE,
) -> Any:
    """Call funcao(*args) in a child process and return its result.

    `funcao` must be a module-level function (spawn imports it by name).
    Raises FalhaIsolada if it raises, runs past the deadline or dies.
    """
    ctx = multiprocessing.get_context("spawn")
    receptor, emissor = ctx.Pipe(duplex=False)
    filho = ctx.Process(target=_filho, args=(emissor, funcao, args, memoria_limite), daemon=True)
    filho.start()
    emissor.close()
    try:
        if not receptor.poll(tempo_limite_s):
            raise FalhaIsolada("tempo")
        try:
            ok, valor = receptor.recv()
        except EOFError:
            raise FalhaIsolada("encerrado") from None  # crashed or killed (e.g. out of memory)
    finally:
        if filho.is_alive():
            filho.kill()
        filho.join()
        receptor.close()
    if not ok:
        raise FalhaIsolada(valor)
    return valor


def _filho(emissor, funcao, args, memoria_limite: int) -> None:
    import os
    import resource

    # Numeric libraries reserve memory per thread at import; one is plenty.
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    resource.setrlimit(resource.RLIMIT_AS, (memoria_limite, memoria_limite))
    try:
        resultado = (True, funcao(*args))
    except BaseException as exc:  # noqa: BLE001 — everything is reported to the parent
        resultado = (False, type(exc).__name__)
    emissor.send(resultado)
    emissor.close()
