"""Run untrusted-input parsing in a short-lived child process.

Uploaded PDFs and spreadsheets are parsed by native code (MuPDF, the zip
reader behind openpyxl). A malformed file could crash, hang or exhaust the
memory of the process doing it. The web app runs on a single machine, so a
crash there would take the site down — and, since an interrupted job is
retried, keep taking it down. In a child process the damage is contained:
the parent sees an error and moves on.

The child is a fresh interpreter (`python -I`) started with a scrubbed
environment: it never sees DATABASE_URL, CPF_HMAC_KEY or any other secret.
It gets an address-space limit and a wall-clock deadline. Arguments and
results cross as tagged JSON, never pickle, so a compromised child cannot
make the parent run code: the parent rebuilds only the dataclasses the caller
allows. For an exception only its class name crosses, since its message may
quote document content.

Not covered: the child runs as the same user and still has network access
(see TODO.md). `proteger_processo_pai` makes the parent's /proc entries
(environ, memory) unreadable to it.
"""

from __future__ import annotations

import base64
import dataclasses
import importlib
import json
import os
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Iterable

TEMPO_LIMITE_S = 60
MEMORIA_LIMITE = 2 * 1024**3

# The only variables the child inherits. Everything else, secrets included,
# stays in the parent.
_AMBIENTE_PERMITIDO = ("PATH", "LANG", "LC_ALL", "TZ", "HOME")

# The child reads its request from argv; this bootstrap puts the project on
# sys.path (-I leaves it out) and hands over to _principal.
_INICIO = "import sys; sys.path[:0] = [sys.argv[1]]; import isolamento; isolamento._principal()"


class FalhaIsolada(Exception):
    """The child failed. `motivo` is an exception class name, 'tempo',
    'encerrado' or 'resposta' (an answer the parent would not accept)."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def executar_isolado(
    funcao: Callable[..., Any],
    *args: Any,
    tempo_limite_s: float = TEMPO_LIMITE_S,
    memoria_limite: int = MEMORIA_LIMITE,
    tipos: Iterable[type] = (),
) -> Any:
    """Call funcao(*args) in a child process and return its result.

    `funcao` must be a module-level function (the child imports it by name).
    Arguments and result may hold None, bool, int, float, str, bytes, Decimal,
    date, datetime, lists, dicts with str keys, and — in the result only —
    instances of the dataclasses listed in `tipos`.
    Raises FalhaIsolada if it raises, runs past the deadline or dies.
    """
    pedido = json.dumps({
        "funcao": f"{funcao.__module__}:{funcao.__qualname__}",
        "args": [_codificar(a) for a in args],
        "caminho": [p for p in sys.path if p],
        "memoria": memoria_limite,
    })
    raiz = os.path.dirname(os.path.abspath(__file__))
    ambiente = {k: os.environ[k] for k in _AMBIENTE_PERMITIDO if k in os.environ}
    # Numeric libraries reserve memory per thread at import; one is plenty.
    ambiente.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    try:
        filho = subprocess.run(
            [sys.executable, "-I", "-c", _INICIO, raiz],
            input=pedido.encode(),
            capture_output=True,
            env=ambiente,
            timeout=tempo_limite_s,
            close_fds=True,
        )
    except subprocess.TimeoutExpired:
        raise FalhaIsolada("tempo") from None  # run() has killed the child
    if not filho.stdout:
        raise FalhaIsolada("encerrado")  # crashed or killed (e.g. out of memory)
    try:
        resposta = json.loads(filho.stdout)
        if not resposta["ok"]:
            motivo = resposta["erro"]
            raise FalhaIsolada(motivo if isinstance(motivo, str) and motivo.isidentifier() else "resposta")
        permitidos = {f"{t.__module__}.{t.__qualname__}": t for t in tipos}
        return _decodificar(resposta["valor"], permitidos)
    except FalhaIsolada:
        raise
    except Exception:
        raise FalhaIsolada("resposta") from None


def proteger_processo_pai() -> None:
    """Make this process non-dumpable: its /proc/<pid>/environ and memory are
    then readable only by root, not by a parsing child of the same user.
    Linux only; a no-op elsewhere."""
    try:
        import ctypes

        pr_set_dumpable = 4
        ctypes.CDLL(None, use_errno=True).prctl(pr_set_dumpable, 0, 0, 0, 0)
    except (OSError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Tagged JSON
# ---------------------------------------------------------------------------


def _codificar(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Decimal):
        return {"$": "decimal", "v": str(obj)}
    if isinstance(obj, datetime):
        return {"$": "datetime", "v": obj.isoformat()}
    if isinstance(obj, date):
        return {"$": "date", "v": obj.isoformat()}
    if isinstance(obj, bytes):
        return {"$": "bytes", "v": base64.b64encode(obj).decode()}
    if isinstance(obj, (list, tuple)):
        return [_codificar(x) for x in obj]
    if isinstance(obj, dict):
        if not all(isinstance(k, str) for k in obj):
            raise TypeError("dict keys must be str")
        return {"$": "dict", "v": {k: _codificar(v) for k, v in obj.items()}}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        tipo = type(obj)
        return {
            "$": "dataclass",
            "tipo": f"{tipo.__module__}.{tipo.__qualname__}",
            "v": {f.name: _codificar(getattr(obj, f.name)) for f in dataclasses.fields(obj)},
        }
    raise TypeError(f"cannot cross the process boundary: {type(obj).__name__}")


def _decodificar(obj: Any, permitidos: dict[str, type]) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, list):
        return [_decodificar(x, permitidos) for x in obj]
    if not isinstance(obj, dict):
        raise ValueError("unexpected value")
    marca, valor = obj.get("$"), obj.get("v")
    if marca == "decimal":
        return Decimal(valor)
    if marca == "datetime":
        return datetime.fromisoformat(valor)
    if marca == "date":
        return date.fromisoformat(valor)
    if marca == "bytes":
        return base64.b64decode(valor, validate=True)
    if marca == "dict":
        return {str(k): _decodificar(v, permitidos) for k, v in valor.items()}
    if marca == "dataclass":
        tipo = permitidos.get(obj.get("tipo"))
        if tipo is None:
            raise ValueError("type not allowed")
        campos = {f.name for f in dataclasses.fields(tipo)}
        if set(valor) != campos:
            raise ValueError("fields do not match")
        return tipo(**{k: _decodificar(v, permitidos) for k, v in valor.items()})
    raise ValueError("unknown tag")


# ---------------------------------------------------------------------------
# Child side
# ---------------------------------------------------------------------------


def _principal() -> None:
    import resource

    # Keep a private handle on the real stdout for the answer, and send fd 1
    # to stderr, so a library printing while it parses cannot corrupt it.
    saida = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    pedido = json.loads(sys.stdin.read())
    memoria = int(pedido["memoria"])
    resource.setrlimit(resource.RLIMIT_AS, (memoria, memoria))
    # The address-space cap is above a small machine's RAM (virtual memory
    # runs well past resident), so the kernel's OOM killer may act first:
    # make this process its first choice, not the web server.
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("1000")
    except OSError:
        pass  # not Linux, or no procfs
    # Where the caller's modules live (tests import theirs by name).
    sys.path[:] = [p for p in pedido["caminho"] if isinstance(p, str)] + sys.path

    try:
        modulo, nome = pedido["funcao"].split(":")
        funcao = importlib.import_module(modulo)
        for parte in nome.split("."):
            funcao = getattr(funcao, parte)
        args = [_decodificar(a, {}) for a in pedido["args"]]
        resposta = {"ok": True, "valor": _codificar(funcao(*args))}
    except BaseException as exc:  # noqa: BLE001 — everything is reported to the parent
        resposta = {"ok": False, "erro": type(exc).__name__}
    saida.write(json.dumps(resposta))
    saida.flush()
