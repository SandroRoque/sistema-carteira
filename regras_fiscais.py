"""Brazilian tax rules the app applies, each with its official source.

Every rate, limit and code used anywhere in the app lives here, next to
the norm it comes from, so a yearly review only touches this file (see
TODO.md, "IRPF codes must be verified"). `VERIFICADO` says whether the
rule was checked against the primary source; anything False must be
confirmed before it is relied on, and the UI should say so.

Primary sources consulted on 2026-10-06 (day trade: 2026-10-08):
  PR-IRPF-2026  Receita Federal, "Perguntas e Respostas IRPF 2026", v1.00
                (2026-04-23), questions 705-715, 730.
  IN-1585       Instrução Normativa RFB nº 1.585/2015, arts. 37 and 56.
  L-9430        Lei nº 9.430/1996, art. 68.
  L-8668        Lei nº 8.668/1993, art. 20-D (incluído pela Lei nº 14.130/2021),
                texto atualizado no portal da Câmara dos Deputados.
  PGD-2026      Programa IRPF 2026 v1.5 (Receita Federal): tabelas
                lib/resources/tipoBens.xml (vigência 05/03/2026),
                tipoRendIsento.xml, tipoRendTributExclusiva.xml, and its
                help, AjudaIRPF.pdf ("Ajuda"), consulted on 2026-10-06.
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

# ETFs (fundos de índice de ações, BOVA11, IVVB11): regular operations at
# 15%, never the stock exemption.
ETF_SEM_ISENCAO = Regra(
    "ETFs de ações tributados sem isenção",
    "PR-IRPF-2026 q.707 (Atenção); IN-1585 art. 59, §2º",
    True,
)

# Fixed-income ETFs (Lei 13.043/2014 art. 2º, e.g. IMAB11) are taxed as
# fixed income, and infrastructure funds (Lei 12.431/2011) at 0% for
# individuals: neither goes in the monthly DARF, so the app leaves them out
# (tipo 'fundo', and 'etf' with subtipo 'renda_fixa').
FORA_DO_DARF_MENSAL = Regra(
    "ETFs de renda fixa e fundos de infraestrutura fora da apuração mensal",
    "PR-IRPF-2026 q.736 (Atenção 1 e 2), q.738, V",
    True,
)

# Fiagro quota sales: 20%, under the variable-income rules, like FIIs.
ALIQUOTA_FIAGRO = Regra(Decimal("0.20"), "L-8668 art. 20-D", True)
# Whether Fiagro and FII losses offset each other: assumed yes (same 20%
# bucket) until confirmed.
COMPENSACAO_FIAGRO_COM_FII = Regra("Fiagro com FII", "a confirmar", False)

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

# Incorporação, fusão, conversão: the new shares or quotas take over the
# cost of the old ones; no sale and no tax at the event. No source found yet.
# Lei 14.754/2023 art. 30, §2º (no IRRF on mergers of funds of the same
# regime) does NOT cover FIIs or Fiagros: its art. 39, I leaves them under
# Lei 8.668/1993 (Planalto, read 2026-10-08). For stocks, the Receita treats
# incorporação de ações as a sale taxed as ganho de capital (PR-IRPF-2026
# q.603). See TODO.md.
CONVERSAO_CUSTO_TRANSFERIDO = Regra(
    "custo das antigas passa às novas",
    "sem fonte para FIIs (Lei 14.754/2023 art. 39, I exclui FIIs); ações: PR-IRPF-2026 q.603 trata como alienação",
    False,
)

# ── DARF ──────────────────────────────────────────────────────────────────

CODIGO_DARF_RENDA_VARIAVEL = Regra("6015", "PR-IRPF-2026 q.730", True)
# Due on the last business day of the month after the gain.
VENCIMENTO = Regra("último dia útil do mês subsequente", "PR-IRPF-2026 q.730; IN-1585 art. 56, §5º", True)
# A DARF below R$ 10,00 is not issued: the amount carries to later months.
DARF_MINIMO = Regra(Decimal("10.00"), "L-9430 art. 68", True)

# ── Day trade ─────────────────────────────────────────────────────────────
#
# Bought and sold the same day, same ativo, same broker (q.705; matched in
# custo_medio). Gains taxed at 20%, never exempt (q.707, Atenção); losses
# offset only day-trade gains, in the month or later (q.709, Atenção).
ALIQUOTA_DAY_TRADE = Regra(Decimal("0.20"), "PR-IRPF-2026 q.706, a; IN-1585 art. 65", True)
DAY_TRADE_SEM_ISENCAO = Regra("sem isenção", "PR-IRPF-2026 q.707 (Atenção)", True)
COMPENSACAO_DAY_TRADE = Regra("day trade só com day trade", "PR-IRPF-2026 q.709 (Atenção); IN-1585 art. 64", True)
# The broker withholds 1% of the day's positive day-trade result (same-day
# losses offset first, q.705). Deductible from the month's tax and, if left
# over, from later months of the same calendar year only (q.714, q.715).
# The documents do not carry it: the app estimates it from the trades.
IRRF_DAY_TRADE = Regra(Decimal("0.01"), "PR-IRPF-2026 q.712-715; Lei 9.959/2000 art. 8º; IN-1585 art. 65", True)


# ── Declaração anual (IRPF): fichas e códigos ─────────────────────────────
#
# Bens e Direitos: (grupo, código). CNPJ is mandatory for groups 03 and 07,
# not for 04-04 (Ajuda p.183). A listed asset carries its ticker as
# "Código de Negociação" (Ajuda p.184). Assets are declared at cost.

BEM_ACOES = Regra(("03", "01"), "PGD-2026 tipoBens: 03-01 Ações (inclusive as listadas em bolsa)", True)
# Units are certificates of deposit of shares: declared as shares by the
# usual reading of 03-01, which the table does not spell out.
BEM_UNITS = Regra(("03", "01"), "a confirmar: a tabela não cita units", False)
BEM_TITULOS_TRIBUTAVEIS = Regra(
    ("04", "02"), "PGD-2026 tipoBens: 04-02 Títulos públicos e privados sujeitos à tributação (Tesouro Direto, CDB, RDB e Outros)", True)
BEM_TITULOS_ISENTOS = Regra(
    ("04", "03"), "PGD-2026 tipoBens: 04-03 Títulos isentos de tributação (LCI, LCA, LCD, CRI, CRA, LIG, Debêntures de Infraestrutura e outros)", True)
BEM_BDR = Regra(("04", "04"), "PGD-2026 tipoBens: 04-04 Ativos negociados em bolsa no Brasil (BDRs, opções...)", True)
BEM_FIAGRO = Regra(("07", "02"), "PGD-2026 tipoBens: 07-02 Fiagro - Lei 8.668/1993", True)
BEM_FII = Regra(("07", "03"), "PGD-2026 tipoBens: 07-03 Fundos de Investimento Imobiliário (FII)", True)
BEM_ETF = Regra(("07", "06"), "PGD-2026 tipoBens: 07-06 ... ETF - Entidade de investimento - Lei 14.754/2023", True)
BEM_ETF_RENDA_FIXA = Regra(("07", "08"), "PGD-2026 tipoBens: 07-08 Fundos de Índice de Renda Fixa (ETFs)", True)
BEM_FUNDO_INFRA = Regra(
    ("07", "10"), "PGD-2026 tipoBens: 07-10 Fundos de Infraestrutura, FIDC e outros (alíquota 0%) - Lei 12.431", True)

# Rendimentos Isentos e Não Tributáveis (line codes of the 2026 program).
ISENTO_DIVIDENDOS = Regra("09", "PGD-2026 tipoRendIsento; Ajuda p.87: 09 - Lucros e dividendos recebidos", True)
ISENTO_ACOES_ATE_20_MIL = Regra(
    "20", "Ajuda p.103: 20 - Ganhos líquidos ... ações ... até R$ 20.000,00 em cada mês", True)
# FII and Fiagro distributions have no line of their own: "99 - Outros",
# which can also be filled from the 07-02/07-03 asset (Ajuda p.113, Atenção 4).
ISENTO_RENDIMENTOS_FII = Regra("99", "Ajuda p.111-113: 99 - Outros, Atenção 4; PR-IRPF-2026 q.738, VII", True)

# Income of exempt bonds (LCI, LCA, CRI, CRA...). Shown as a pointer only: the
# app does not compute fixed-income income.
ISENTO_LCI_LCA = Regra("12", "PGD-2026 tipoRendIsento: 12 - Rendimentos de poupanças, letras hipotecárias, LCI, LCA, CRI, CRA", True)

# Rendimentos Sujeitos à Tributação Exclusiva/Definitiva.
# Income of taxed bonds and Tesouro, net of the tax withheld at source.
EXCLUSIVO_APLICACOES = Regra("06", "PGD-2026 tipoRendTributExclusiva; Ajuda p.116: 06 - Rendimentos de aplicações financeiras", True)
EXCLUSIVO_JCP = Regra("10", "PGD-2026 tipoRendTributExclusiva; Ajuda p.118: 10 - Juros sobre capital próprio", True)
