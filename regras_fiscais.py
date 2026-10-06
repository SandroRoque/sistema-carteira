"""Brazilian tax rules the app applies, each with its official source.

Every rate, limit and code used anywhere in the app lives here, next to
the norm it comes from, so a yearly review only touches this file (see
TODO.md, "IRPF codes must be verified"). `VERIFICADO` says whether the
rule was checked against the primary source; anything False must be
confirmed before it is relied on, and the UI should say so.

Primary sources consulted on 2026-10-06:
  PR-IRPF-2026  Receita Federal, "Perguntas e Respostas IRPF 2026", v1.00
                (2026-04-23), questions 706, 707, 709, 730.
  IN-1585       Instrução Normativa RFB nº 1.585/2015, arts. 37 and 56.
  L-9430        Lei nº 9.430/1996, art. 68.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Regra:
    valor: Decimal | str
    fonte: str
    verificado: bool


# ── Mercado à vista em bolsa, pessoa física (operações comuns) ────────────

# Gains on regular (non day trade) operations: 15%.
ALIQUOTA_OPERACOES_COMUNS = Regra(
    Decimal("0.15"), "PR-IRPF-2026 q.706, b; Lei 11.033/2004 art. 2º, II", True)

# Gains on FII quota sales: 20%.
ALIQUOTA_FII = Regra(Decimal("0.20"), "IN-1585 art. 37, caput; Lei 8.668/1993 art. 18", True)

# Exemption: gains on STOCKS (mercado à vista) when the month's total stock
# sales do not exceed R$ 20.000. Only stocks (and gold): not FIIs, ETFs or
# BDRs. Losses of exempt months can still offset taxable gains (PR q.729,
# note on the Demonstrativo).
LIMITE_ISENCAO_ACOES = Regra(Decimal("20000"), "PR-IRPF-2026 q.707, I; Lei 11.033/2004 art. 3º, I", True)

# Units (certificados de depósito de ações, e.g. TAEE11) are not stocks for
# the exemption: taxed at 15% from the first real.
UNITS_SEM_ISENCAO = Regra(
    "units tributadas sem isenção",
    "Solução de Consulta COSIT nº 145/2021 (via fonte secundária; conferir o texto)",
    False,
)

# Subscription rights sold in bolsa: treated as regular operations without
# the exemption until confirmed.
DIREITOS_SEM_ISENCAO = Regra("direitos de subscrição sem isenção", "a confirmar", False)

# Withholding of 0,005% on sales, not withheld when ≤ R$ 1,00; deductible
# from the month's tax.
IRRF_ALIQUOTA = Regra(Decimal("0.00005"), "PR-IRPF-2026 q.706; Lei 11.033/2004 art. 2º, §1º", True)
IRRF_DISPENSA_ATE = Regra(Decimal("1.00"), "PR-IRPF-2026 q.706", True)

# Losses offset gains of the same kind, in the month or later months:
# regular operations among themselves; FII losses only against FII gains.
COMPENSACAO_COMUNS = Regra("comuns com comuns", "PR-IRPF-2026 q.709; RIR/2018 art. 841 §2º", True)
COMPENSACAO_FII = Regra("FII só com FII", "IN-1585 art. 37, §2º", True)

# ── DARF ──────────────────────────────────────────────────────────────────

CODIGO_DARF_RENDA_VARIAVEL = Regra("6015", "PR-IRPF-2026 q.730", True)
# Due on the last business day of the month after the gain.
VENCIMENTO = Regra("último dia útil do mês subsequente", "PR-IRPF-2026 q.730; IN-1585 art. 56, §5º", True)
# A DARF below R$ 10,00 is not issued: the amount carries to later months.
DARF_MINIMO = Regra(Decimal("10.00"), "L-9430 art. 68", True)

# Not computed by the app.
DAY_TRADE = Regra("não apurado", "PR-IRPF-2026 q.706, a (20%)", True)
