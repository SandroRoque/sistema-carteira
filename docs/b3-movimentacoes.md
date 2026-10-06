# B3 Movimentações Report

B3 lets you export a full transaction history from their portal as an xlsx file
(`movimentacao-<timestamp>.xlsx`).  Drop any such file in `b3-reports/` and the
loader will pick it up.

## File structure

One sheet (`Movimentação`), 8 columns:

| Column | Example |
|---|---|
| `Entrada/Saída` | `Credito` / `Debito` |
| `Data` | `30/12/2025` (DD/MM/YYYY) |
| `Movimentação` | event type string |
| `Produto` | `PETR4 - PETROLEO BRASILEIRO S/A - PETROBRAS` |
| `Instituição` | `NU INVEST CORRETORA DE VALORES S.A.` |
| `Quantidade` | `20` (numeric) or `0` |
| `Preço unitário` | `31.40` (float) or `'-'` |
| `Valor da Operação` | `628.0` (float) or `'-'` |

## Event taxonomy

### Cash income — stored in `proventos`

| Movimentação | Description |
|---|---|
| `Dividendo` | Cash dividend |
| `Juros Sobre Capital Próprio` | JCP — Brazil-specific interest on net equity, taxed at source |
| `Rendimento` | FII monthly income distribution |
| `Resgate` | Mandatory redemption at a stated price (e.g. share class conversion) |

These always carry a numeric `Valor da Operação`.

### Non-cash corporate actions — stored in `eventos_corporativos`

| Movimentação | Description |
|---|---|
| `Bonificação em Ativos` | Bonus shares (stock dividend).  Quantity = shares credited.  Cost basis = R$0 for tax purposes. |
| `Atualização` | **Position snapshot**, not a delta (see note below). |
| `Fração em Ativos` | Fractional share credited after a corporate action. |
| `Leilão de Fração` | Fractional share cash-out via B3 auction. |

#### ⚠️ `Atualização` is a snapshot, not a transaction

The quantity in an `Atualização` row is the **total position at that date**, not the
number of shares added or removed.  Evidence: one position appears in several
snapshots months apart with the same quantity while nothing was traded, and
another steps up between snapshots by exactly the shares bought in between.

These events are triggered by B3 whenever a corporate action or custody transfer
causes a position reconciliation.  They are useful for auditing position
continuity but should **not** be included in running position sums computed from
`operacoes`.

### Subscription rights — stored in `subscricoes`

Events travel in lifecycle order: grant → (exercise | expiry).

| Movimentação | `Entrada/Saída` | Meaning |
|---|---|---|
| `Direito de Subscrição` | Credito | Rights granted |
| `Direito Sobras de Subscrição` | Credito | Leftover rights granted |
| `Direitos de Subscrição - Exercido` | Debito | Rights exercised (cash out) |
| `Direitos de Subscrição - Não Exercido` | Debito | Rights expired unexercised |
| `Direito Sobras de Subscrição - Não Exercido` | Debito | Leftover rights expired |
| `Solicitação de Subscrição` | Credito | Subscription request lodged |
| `Recibo de Subscrição` | Credito | Subscription receipt (shares pending) |

The new shares arrive later as an `Atualização` Credito on the stock itself;
B3 never debits the receipt. `subscricoes.py` matches each exercise with the
next credit of the same company and quantity (within 180 days): the amount
paid becomes the cost of those shares and the receipt closes.

### Trade events — reconciled with the notes

These rows describe the same purchases and sales captured from the PDF notas de
corretagem. The notes are richer (fees per trade), so they are the source of
price and cost. `cobertura.conciliar` matches each equity `Transferência -
Liquidação` with the notes of that ativo in the 7 days before it: a settlement
with no note at all is added to the position at the B3 gross value (flagged
"sem nota"); one whose quantity disagrees with the notes is only listed.

| Movimentação | Corresponds to |
|---|---|
| `Transferência - Liquidação` | Equity / FII / FI buy settled at B3 |
| `Compra` | Tesouro Direto purchase |
| `COMPRA / VENDA` | Fixed-income (CDB, LCI, etc.) buy |
| `VENCIMENTO` | CDB maturity — stored as `tipo='vencimento'` in `operacoes` so position calculations see the holding go to zero |

## `Produto` field parsing

The format is consistent across all event types:

```
"TICKER - COMPANY NAME"         →  ticker = first token ("PETR4", "HGLG11", …)
"TYPE - ID - ISSUER"            →  fixed income; type = "CDB"/"LCI"/…, id = second token
"Tesouro IPCA+ 2029"            →  Tesouro Direto; name used as-is
```

Resolution order:
1. Look up full `Produto` string in `ticker_aliases` → use existing `ativo_id`.
2. Extract first token; if it matches a B3 equity/FII ticker pattern, look up in
   `ativos` by `ticker`.
3. Otherwise auto-create an unreviewed `ativo` (`revisado=0`) and add a
   `ticker_aliases` entry.

## Institution → `corretora_id` mapping

B3 uses slightly different institution name strings across time:

| `Instituição` | `corretora_id` |
|---|---|
| `NU INVEST CORRETORA DE VALORES S.A.` | `nu_invest` |
| `NU INVESTIMENTOS S.A. - CTVM` | `nu_invest` |
