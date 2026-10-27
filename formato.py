"""Brazilian number and date formatting for the web pages.

R$ 1.234,56 · 12,4% · 1.250 cotas · 06/10/2026. Money is rounded to the
cent half up, the usual convention for amounts shown to people (Decimal's
own formatting rounds half to even). Negative values use the minus sign
(U+2212), which reads better than a hyphen in tabular figures.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
MENOS = "−"
MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")

Numero = Decimal | int | float


def _decimal(v: Numero) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def numero(v: Numero, casas: int = 2) -> str:
    """1234.5 → '1.234,50' (absolute value; callers add the sign)."""
    q = abs(_decimal(v)).quantize(Decimal(1).scaleb(-casas), ROUND_HALF_UP)
    texto = f"{q:,.{casas}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def _negativo(v: Numero, casas: int) -> bool:
    # A value that rounds to zero is shown without a sign.
    return _decimal(v).quantize(Decimal(1).scaleb(-casas), ROUND_HALF_UP) < 0


def brl(v: Numero | None) -> str:
    if v is None:
        return "—"
    return (f"{MENOS} " if _negativo(v, 2) else "") + "R$ " + numero(v)


def brl_sinal(v: Numero | None) -> str:
    """Gains and losses: '+ R$ 2.175,00' / '− R$ 1.080,00'."""
    if v is None:
        return "—"
    if _negativo(v, 2):
        return f"{MENOS} R$ {numero(v)}"
    if _decimal(v).quantize(Decimal("0.01"), ROUND_HALF_UP) == 0:
        return "R$ 0,00"
    return f"+ R$ {numero(v)}"


def pct(fracao: Numero | None, casas: int = 1, sinal: bool = False) -> str:
    """A fraction as percent: 0.124 → '12,4%' ('+12,4%' with sinal)."""
    if fracao is None:
        return "—"
    v = _decimal(fracao) * 100
    texto = numero(v, casas) + "%"
    if _negativo(v, casas):
        return MENOS + texto
    if sinal and v.quantize(Decimal(1).scaleb(-casas), ROUND_HALF_UP) > 0:
        return "+" + texto
    return texto


def qtd(v: Numero | None) -> str:
    """Quantities: '1.250', '0,79', '12,3400' → '12,34'."""
    if v is None:
        return "—"
    d = _decimal(v)
    if d == d.to_integral_value():
        texto = numero(d, 0)
    else:
        texto = numero(d, 4).rstrip("0").rstrip(",")
    return (MENOS if d < 0 else "") + texto


def data(v: date | None) -> str:
    return "—" if v is None else v.strftime("%d/%m/%Y")


def data_hora(v: datetime | None) -> str:
    return "—" if v is None else v.astimezone(FUSO).strftime("%d/%m/%Y %H:%M")


def quando(v: datetime | None, hoje: date | None = None) -> str:
    """'hoje, 15:42' for today, else '05/10/2026 15:42'."""
    if v is None:
        return "—"
    local = v.astimezone(FUSO)
    if local.date() == (hoje or datetime.now(FUSO).date()):
        return f"hoje, {local:%H:%M}"
    return f"{local:%d/%m/%Y %H:%M}"


def hora(v: datetime | None) -> str:
    return "—" if v is None else v.astimezone(FUSO).strftime("%H:%M")


def mes_ano(v: date) -> str:
    return f"{MESES[v.month - 1]}/{v.year}"


def ler_decimal(texto: str) -> Decimal:
    """Parse what a person types: '1.234,56', '1234,56', '18.04', 'R$ 2,5'.
    A lone '.' with up to two decimals is read as the decimal point; dots
    otherwise group thousands. Raises ValueError on anything else."""
    t = texto.strip().removeprefix("R$").strip().replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") == 1 and len(t.split(".")[1]) <= 2:
        pass
    else:
        t = t.replace(".", "")
    try:
        valor = Decimal(t)
    except InvalidOperation:
        raise ValueError(f"valor inválido: {texto!r}") from None
    if not valor.is_finite():
        raise ValueError(f"valor inválido: {texto!r}")
    return valor
