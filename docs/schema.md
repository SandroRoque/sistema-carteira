# Database Schema

`carteira.db` — SQLite database for the investment portfolio system.

All monetary values are in **BRL** unless otherwise noted.  
All dates are stored as **ISO 8601 strings** (`YYYY-MM-DD`).  
Booleans are stored as **INTEGER** (`0` = false, `1` = true).

---

## Data Sources

| Table | Populated by | Source |
|---|---|---|
| `ativos` | `carrega_notas.py`, `carrega_b3.py` | Auto-created on first encounter from PDFs and B3 reports |
| `ticker_aliases` | `carrega_notas.py`, `carrega_b3.py` | Auto-created alongside `ativos` |
| `notas` | `carrega_notas.py` | One row per source PDF (all doc types) |
| `negociacoes` | `carrega_notas.py` | One row per trade line extracted from PDFs |
| `b3_movimentacoes` | `carrega_b3.py` | B3 movimentações Excel exports |
| `b3_arquivos_processados` | `carrega_b3.py` | Audit log of loaded files |

---

## Entity Relationship

```mermaid
erDiagram
    ativos {
        int  id                      PK
        text tipo
        text subtipo
        text ticker
        text nome
        text cnpj_emissor
        text emissor
        text indexador
        real taxa_prefixada
        real percentual_do_indexador
        text emissao
        text vencimento
        int  revisado
    }
    ticker_aliases {
        text raw_text  PK
        int  ativo_id  FK
    }
    notas {
        text nota_id       PK
        text corretora_id  PK
        text doc_type      PK
        text data_pregao
        text cpf_cliente
        text codigo_cliente
    }
    negociacoes {
        int  id             PK
        text nota_id        FK
        text corretora_id   FK
        text doc_type       FK
        int  linha_na_nota
        int  ativo_id       FK
        text data
        text sentido
        text tipo
        real quantidade
        real preco_unitario
        real valor_bruto
        real taxas_proporcionais
        real valor_liquido
    }
    b3_movimentacoes {
        int  id              PK
        text sentido
        text data
        text movimentacao
        text produto_raw
        int  ativo_id        FK
        text instituicao
        real quantidade
        real preco_unitario
        real valor
        text arquivo         FK
    }
    b3_arquivos_processados {
        text arquivo       PK
        text processado_em
    }

    ativos                  ||--o{ ticker_aliases      : "identified by"
    ativos                  ||--o{ negociacoes         : "traded in"
    notas                   ||--o{ negociacoes         : "contains"
    ativos                  |o--o{ b3_movimentacoes    : "appears in"
    b3_arquivos_processados ||--o{ b3_movimentacoes    : "sourced from"
```

---

## `notas`

One row per source document.  
Covers all three document types: `NotaCorretagem`, `TituloPublico`, `TituloPrivado`.

| Column | Type | Nullable | Source | Description |
|---|---|---|---|---|
| `nota_id` | TEXT PK | NO | all | Nota number as printed on the document |
| `corretora_id` | TEXT PK | NO | all | Broker identifier (`nu_invest`, `xp`, `safra`, `brasil_plural`) |
| `doc_type` | TEXT PK | NO | all | `NotaCorretagem` / `TituloPublico` / `TituloPrivado` |
| `data_pregao` | TEXT | NO | all | Trade / operation date (`YYYY-MM-DD`) |
| `data_de_liquidacao` | TEXT | YES | TituloPrivado | Settlement date; differs from `data_pregao` for fixed income |
| `cpf_cliente` | TEXT | NO | all | Investor CPF (normalized, digits only) |
| `codigo_cliente` | TEXT | NO | all | Broker-assigned client code |
| `nome_cliente` | TEXT | YES | all | Client full name |
| `assessor` | TEXT | YES | NotaCorretagem | Advisor/agent code (XP, Safra, Brasil Plural) |
| `folha` | TEXT | YES | NotaCorretagem | Page number within a multi-page nota |
| `endereco` | TEXT | YES | NotaCorretagem | Street address (Nu Invest only) |
| `cidade` | TEXT | YES | NotaCorretagem | City (Nu Invest only) |
| `uf` | TEXT | YES | NotaCorretagem | State (Nu Invest only) |
| `cep` | TEXT | YES | NotaCorretagem | Postal code (Nu Invest only) |
| `nota_de` | TEXT | YES | TituloPrivado | Raw operation label from the document (e.g. `VENDA FINAL`, `RESGATE`). Source for `negociacoes.tipo` inference |
| `local` | TEXT | YES | TituloPrivado | Trading venue / platform |
| `emissor` | TEXT | YES | TituloPrivado | Issuing institution name |
| `cnpj_emissor` | TEXT | YES | TituloPrivado | CNPJ of the issuing institution |
| `comando` | TEXT | YES | TituloPrivado | Internal operation command code |
| `mercado` | TEXT | YES | TituloPublico | Market segment (e.g. `Tesouro Direto`) |
| `status` | TEXT | YES | TituloPublico | Operation status (e.g. `Pago`, `Pendente`) |
| `liquido_para` | REAL | YES | all | Net amount the client pays or receives to settle this nota |
| `filename` | TEXT | NO | — | Source PDF filename |
| `processado_em` | TEXT | NO | — | UTC timestamp of first load |
| **Resumo dos Negócios** | | | | *(NotaCorretagem only)* |
| `debentures` | REAL | YES | NotaCorretagem | Debêntures volume |
| `vendas_a_vista` | REAL | YES | NotaCorretagem | Total value of equity sales |
| `compras_a_vista` | REAL | YES | NotaCorretagem | Total value of equity purchases |
| `opcoes_compras` | REAL | YES | NotaCorretagem | Options purchases value |
| `opcoes_vendas` | REAL | YES | NotaCorretagem | Options sales value |
| `operacoes_a_termo` | REAL | YES | NotaCorretagem | Forward / term contracts value |
| `valor_das_operacoes_com_titulos_publicos` | REAL | YES | NotaCorretagem | Tesouro Direto volume (nominal) |
| `valor_das_operacoes` | REAL | YES | NotaCorretagem | Grand total of all operations |
| `valor_liquido_das_operacoes` | REAL | YES | NotaCorretagem | Net value after netting buys and sells |
| **Resumo Financeiro** | | | | *(NotaCorretagem only)* |
| `taxa_de_liquidacao` | REAL | YES | NotaCorretagem | B3 clearing / settlement fee |
| `taxa_de_registro` | REAL | YES | NotaCorretagem | B3 registration fee |
| `total_clearing_cblc` | REAL | YES | NotaCorretagem | Total CBLC/clearing charges subtotal |
| `taxa_de_termo_opcoes` | REAL | YES | NotaCorretagem | Term / options fee |
| `taxa_a_n_a` | REAL | YES | NotaCorretagem | ANA fee |
| `emolumentos` | REAL | YES | NotaCorretagem | Exchange emoluments |
| `total_bolsa` | REAL | YES | NotaCorretagem | Total exchange charges subtotal |
| `corretagem` | REAL | YES | NotaCorretagem | Brokerage commission |
| `iss` | REAL | YES | NotaCorretagem | ISS municipal services tax |
| `irrf_sobre_operacoes` | REAL | YES | NotaCorretagem | IRRF withheld on day-trades |
| `outras` | REAL | YES | NotaCorretagem | Other charges |
| `total_corretagem_despesas` | REAL | YES | NotaCorretagem | Total brokerage + fees subtotal |
| `taxa_operacional` | REAL | YES | NotaCorretagem (XP) | XP operational fee |
| `execucao` | REAL | YES | NotaCorretagem (XP, Safra) | Execution fee |
| `taxa_de_custodia` | REAL | YES | NotaCorretagem (XP) | XP custody fee |
| `impostos` | REAL | YES | NotaCorretagem (XP) | XP taxes line |
| `pis_cofins` | REAL | YES | NotaCorretagem (Safra) | Safra PIS/COFINS |
| `taxa_de_transferencia_de_ativos` | REAL | YES | NotaCorretagem (Safra) | Safra asset transfer fee |
| `execucao_casa` | REAL | YES | NotaCorretagem (Safra) | Safra in-house execution fee |

**Primary key:** `(nota_id, corretora_id, doc_type)`

---

## `negociacoes`

One row per trade line.  
For `NotaCorretagem` a nota may have many rows (one per line in the *Negócios Realizados* table).  
For `TituloPublico` and `TituloPrivado` a nota always has exactly one row.

| Column | Type | Nullable | Source | Description |
|---|---|---|---|---|
| `id` | INTEGER PK | NO | — | Auto-increment surrogate key |
| `nota_id` | TEXT | NO | all | FK → `notas.nota_id` |
| `corretora_id` | TEXT | NO | all | FK → `notas.corretora_id` |
| `doc_type` | TEXT | NO | all | FK → `notas.doc_type` |
| `linha_na_nota` | INTEGER | NO | all | 0-based index of this line within the nota. Always `0` for single-trade documents |
| `ativo_id` | INTEGER | NO | — | FK → `ativos.id` |
| `data` | TEXT | NO | all | Trade / operation date (`YYYY-MM-DD`). Denormalized from `notas.data_pregao` for query convenience |
| `sentido` | TEXT | NO | all | `entrada` (buy / aquisicao) or `saida` (sell / resgate). Derived from `tipo` |
| `tipo` | TEXT | NO | all | `compra` / `venda` / `aquisicao` / `resgate`. See [negociacoes.tipo](#negociacoestipo) |
| `debito_credito` | TEXT | YES | NotaCorretagem | `D` (cash outflow) or `C` (cash inflow). Present on equity lines; `NULL` for renda fixa |
| `quantidade` | REAL | YES | all | Number of shares / units / nominal amount |
| `preco_unitario` | REAL | YES | all | Unit price in BRL |
| `valor_bruto` | REAL | YES | all | Gross trade value (`quantidade × preco_unitario`) |
| `taxas_proporcionais` | REAL | NO | all | Portion of nota-level fees allocated to this line (proportional to `valor_bruto`). Always `0` for Tesouro Direto |
| `valor_liquido` | REAL | YES | all | Net amount for this trade after fees |
| `mercado` | TEXT | YES | NotaCorretagem | Market segment code (e.g. `BOVESPA`, `BMF`) |
| `tipo_de_mercado` | TEXT | YES | NotaCorretagem | Market type (e.g. `VISTA`, `OPCAO DE COMPRA`) |
| `prazo` | TEXT | YES | NotaCorretagem, TituloPrivado | Expiry or term |
| `observacao` | TEXT | YES | NotaCorretagem | Free-text note from the broker (e.g. `#` for day-trade) |
| `indexador` | TEXT | YES | TituloPrivado | Rate index (e.g. `CDI`, `IPCA`, `PRÉ`) |
| `taxa_cupom_percentual` | REAL | YES | TituloPrivado | Coupon rate in percent |
| `percentual_do_indexador` | REAL | YES | TituloPrivado | Percentage of the index (e.g. `109.5` for 109.5% CDI) |
| `emissao` | TEXT | YES | TituloPrivado | Instrument issuance date (`YYYY-MM-DD`) |
| `vencimento` | TEXT | YES | TituloPrivado | Instrument maturity date (`YYYY-MM-DD`) |
| `custodia` | TEXT | YES | TituloPrivado | Custodian name |
| `tipo_emitente` | TEXT | YES | TituloPrivado | Issuer category (e.g. `Banco`) |
| `conta_bancaria` | TEXT | YES | TituloPrivado | Bank account tied to the operation |
| `rendimentos` | TEXT | YES | TituloPrivado | Accrued interest description |
| `imposto_de_renda_federal` | REAL | YES | TituloPrivado | IR withheld on this operation |
| `iof` | REAL | YES | TituloPrivado | IOF withheld on this operation |
| `tx_bvmf` | REAL | YES | TituloPublico | B3/BVMF transaction fee |
| `tx_agente_custodia` | REAL | YES | TituloPublico | Custody agent fee (percentage) |
| `especificacao_observacao` | TEXT | YES | TituloPrivado | Additional specification or observation from the document |

**Unique constraint:** `(nota_id, corretora_id, doc_type, linha_na_nota)`

---

## `ativos`

One row per unique investable instrument.  
Rows are created automatically on first encounter; most type-specific fields require manual review (`revisado = 1` signals the row has been verified).

| Column | Type | Nullable | Auto? | Description |
|---|---|---|---|---|
| `id` | INTEGER PK | NO | yes | Auto-increment surrogate key |
| `tipo` | TEXT | NO | yes | Asset class: `acao` / `fii` / `bdr` / `tesouro_direto` / `renda_fixa`. Inferred from ticker pattern or doc_type |
| `subtipo` | TEXT | YES | **manual** | Instrument category. For `renda_fixa`: `CDB` / `LCI` / `LCA` / `debenture` / `CRI` / `CRA`. For `tesouro_direto`: `LTN` / `NTN-B` / `NTN-F` / `LFT`. `NULL` for equity |
| `ticker` | TEXT | YES (UNIQUE) | yes | Canonical ticker symbol. Populated for `acao`, `fii`, `bdr`. Always `NULL` for fixed income |
| `nome` | TEXT | YES | partial | Human-readable name. For fixed income: copied from `titulo` on first load. For equity: `NULL` until set manually |
| `cnpj_emissor` | TEXT | YES | yes (renda_fixa) | Issuer CNPJ. Auto-populated from `TituloPrivado` |
| `emissor` | TEXT | YES | yes (renda_fixa) | Issuer institution name. Auto-populated from `TituloPrivado.emissor` |
| `indexador` | TEXT | YES | yes (renda_fixa) | Rate benchmark: `PRÉ` / `CDI` / `IPCA` / `SELIC` / `IGPM` |
| `taxa_prefixada` | REAL | YES | yes (renda_fixa) | Fixed or spread component in % p.a. |
| `percentual_do_indexador` | REAL | YES | yes (renda_fixa) | Floating component as % of index (e.g. `109.5` for 109.5% CDI) |
| `emissao` | TEXT | YES | yes (renda_fixa) | Issuance date (`YYYY-MM-DD`) |
| `vencimento` | TEXT | YES | yes (renda_fixa) | Maturity date (`YYYY-MM-DD`) |
| `revisado` | INTEGER | NO | — | `0` = auto-created, not yet reviewed. `1` = manually verified |

### Column applicability by type

| Column | acao / fii / bdr | tesouro_direto | renda_fixa |
|---|---|---|---|
| `ticker` | ✓ auto | ✗ | ✗ |
| `nome` | manual | auto | auto |
| `subtipo` | ✗ | manual | manual |
| `cnpj_emissor` | manual | ✗ | ✓ auto |
| `emissor` | ✗ | ✗ | ✓ auto |
| `indexador` | ✗ | manual | ✓ auto |
| `taxa_prefixada` | ✗ | manual | auto if `PRÉ` or `IPCA+` |
| `percentual_do_indexador` | ✗ | ✗ | auto if floating |
| `emissao` | ✗ | ✗ | ✓ auto |
| `vencimento` | ✗ | manual | ✓ auto |

**Primary key:** `id`

---

## `b3_movimentacoes`

One row per line in a B3 movimentações Excel report.  
Covers all event categories B3 reports in a single table: trades, income, corporate actions, and transfers.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | INTEGER PK | NO | Auto-increment surrogate key |
| `sentido` | TEXT | NO | Cash flow direction as reported by B3: `Credito` or `Debito` |
| `data` | TEXT | NO | Event date (`YYYY-MM-DD`) |
| `movimentacao` | TEXT | NO | Event type label from B3 (e.g. `Dividendo`, `Compra`, `Atualização`) |
| `produto_raw` | TEXT | NO | Raw product string from the B3 file (e.g. `PETR4 - PETROLEO BRASILEIRO S.A. PETROBRAS`). Kept as audit trail |
| `ativo_id` | INTEGER | YES | FK → `ativos.id`. `NULL` when the product cannot be resolved |
| `instituicao` | TEXT | YES | Broker / institution name as reported by B3 |
| `quantidade` | REAL | YES | Number of shares or units. `NULL` for income events that carry only a total value |
| `preco_unitario` | REAL | YES | Unit price in BRL. `NULL` for income and corporate action events |
| `valor` | REAL | YES | Total event value in BRL |
| `arquivo` | TEXT | NO | FK → `b3_arquivos_processados.arquivo`. Source filename for traceability |

**Notes**
- `Atualização` rows are **position snapshots**, not deltas. `quantidade` is the total holding at that point in time. Use the most recent per `(ativo_id, instituicao)` to derive current positions.
- Trade rows (`Transferência - Liquidação`, `COMPRA / VENDA`, `Compra`) correspond to broker notes — the same trade also appears in `negociacoes` with full cost-basis detail.
- Deduplication is **date-based**: rows are skipped if any row for that date already exists in this table. This allows safely loading overlapping files without row-level key collisions.

**Primary key:** `id`

---

## `b3_arquivos_processados`

Audit log of B3 Excel files that have been processed. One row per file.  
Used for traceability only — the actual deduplication guard is date-based (see `b3_movimentacoes` notes above).

| Column | Type | Nullable | Description |
|---|---|---|---|
| `arquivo` | TEXT PK | NO | Source filename (e.g. `movimentacao-2026-05-25-18-05-35.xlsx`) |
| `processado_em` | TEXT | NO | UTC timestamp of load (`datetime('now')`) |

**Primary key:** `arquivo`

---

## `ticker_aliases`

Loader-time lookup table. Maps every raw asset description found in a PDF or B3 report to a canonical `ativo`, preventing duplicate rows from being created across multiple loads.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `raw_text` | TEXT PK | NO | Exact string as extracted from the source document |
| `ativo_id` | INTEGER | NO | FK → `ativos.id` |

**Notes**
- The same ativo can have many aliases (ex-dividend suffixes, different broker formatting, B3 long-name format, etc.).
- New raw strings are appended automatically at load time. Users can re-point an alias to a different `ativo_id` to merge mistaken duplicates; `negociacoes.ativo_id` should be updated accordingly.
- **Never delete an alias row** — it is the idempotency key for the loader.

---

## Enumerations

### `ativos.tipo`

| Value | Meaning |
|---|---|
| `acao` | Brazilian equity (Bovespa, including ON and PN shares) |
| `fii` | Fundo de Investimento Imobiliário — REIT equivalent |
| `bdr` | Brazilian Depositary Receipt (foreign company listed on B3) |
| `renda_fixa` | Fixed income instrument (CDB, LCI, LCA, Debenture, etc.) |
| `tesouro_direto` | Brazilian government bond (Tesouro Direto program) |
| `recibo_subscricao` | Subscription receipt — interim certificate between rights exercise and share delivery; eventually converts to the underlying `acao` or `fii` |
| `direito_subscricao` | Subscription right — temporary instrument that is exercised, sold, or expires |
| `desconhecido` | Type could not be inferred automatically; requires manual review |

**Inference rules** (applied automatically at load time):
- `doc_type = TituloPublico` → `tesouro_direto`
- `doc_type = TituloPrivado` → `renda_fixa`
- Canonical ticker ends in `11` → `fii`
- Canonical ticker ends in `12` → `recibo_subscricao`
- Canonical ticker ends in `3[1-9]` (e.g. `34`, `32`) → `bdr`
- Canonical ticker ends in `1`, `2`, or `9` → `direito_subscricao`
- Otherwise → `acao`

---

### `negociacoes.tipo`

| Value | `doc_type` context | Meaning |
|---|---|---|
| `compra` | `NotaCorretagem`, `TituloPublico` | Purchase — cash outflow, asset inflow |
| `venda` | `NotaCorretagem`, `TituloPublico` | Sale — asset outflow, cash inflow |
| `aquisicao` | `TituloPrivado` | Fixed income purchase (e.g. CDB issued to investor). Also mapped from `VENDA FINAL` (Nu Invest label) |
| `resgate` | `TituloPrivado` | Fixed income redemption before or at maturity |

---

### `negociacoes.sentido`

Derived from `tipo` at load time.

| Value | When |
|---|---|
| `entrada` | `tipo` is `compra` or `aquisicao` |
| `saida` | `tipo` is `venda` or `resgate` |

---

### `doc_type`

Used in `notas.doc_type` and `negociacoes.doc_type`.

| Value | Source document |
|---|---|
| `NotaCorretagem` | Equity/FII/BDR trade confirmation note |
| `TituloPublico` | Tesouro Direto transaction note |
| `TituloPrivado` | Fixed income transaction note (CDB, LCI, LCA, etc.) |

---

### `corretora_id`

| Value | Institution |
|---|---|
| `nu_invest` | Nu Investimentos S.A. — CTVM (CNPJ 62.169.875/0001-79) |
| `xp` | XP Investimentos CCTVM S.A. (CNPJ 02.332.886/0001-04) |
| `safra` | Safra DTVM Ltda (CNPJ 01.638.542/0001-57) |
| `brasil_plural` | Brasil Plural CCTVM S/A (CNPJ 05.816.451/0001-15) |

---

### `b3_movimentacoes.sentido`

| Value | Meaning |
|---|---|
| `Credito` | Asset or cash credited to the account |
| `Debito` | Asset or cash debited from the account |

---

### `b3_movimentacoes.movimentacao`

#### Income events — `valor` is the cash amount received

| Value | Meaning |
|---|---|
| `Dividendo` | Cash dividend paid by the company |
| `Juros Sobre Capital Próprio` | JCP — interest on net equity (taxed at 15% withheld at source) |
| `Rendimento` | Monthly FII distribution |

#### Position snapshot — `quantidade` is the total holding, not a delta

| Value | Meaning |
|---|---|
| `Atualização` | Point-in-time position snapshot reported by the custodian |

#### Trade events — cross-reference with `negociacoes` for cost basis

| Value | Meaning |
|---|---|
| `Transferência - Liquidação` | Trade settlement (broker note → B3 settlement) |
| `COMPRA / VENDA` | Combined buy/sell label (used by some brokers) |
| `Compra` | Purchase |

#### Fixed income events

| Value | Meaning |
|---|---|
| `Resgate` | Early or scheduled redemption of a fixed income instrument |
| `VENCIMENTO` | Maturity — instrument reached its scheduled end date |

#### Corporate action events

| Value | Meaning |
|---|---|
| `Bonificação em Ativos` | Stock bonus — new shares issued from retained earnings |
| `Fração em Ativos` | Fractional shares resulting from a corporate action |
| `Leilão de Fração` | Auction of fractional shares; generates a small cash credit |

#### Subscription events

| Value | Meaning |
|---|---|
| `Direito de Subscrição` | Subscription rights granted to existing shareholders |
| `Direitos de Subscrição - Exercido` | Rights were exercised (becomes `Recibo de Subscrição`) |
| `Direitos de Subscrição - Não Exercido` | Rights expired unexercised |
| `Direito Sobras de Subscrição` | Excess subscription rights |
| `Direito Sobras de Subscrição - Não Exercido` | Excess rights expired |
| `Recibo de Subscrição` | Interim holding between rights exercise and new share issuance |
| `Solicitação de Subscrição` | Subscription order submitted |

---

## Key Query Patterns

### Current positions (from B3 snapshots)

```sql
-- Most recent Atualização per asset per institution
SELECT
    a.ticker,
    a.tipo,
    m.instituicao,
    m.quantidade,
    m.data AS snapshot_date
FROM b3_movimentacoes m
JOIN ativos a ON a.id = m.ativo_id
WHERE m.movimentacao = 'Atualização'
  AND m.data = (
      SELECT MAX(m2.data)
      FROM b3_movimentacoes m2
      WHERE m2.ativo_id = m.ativo_id
        AND m2.instituicao = m.instituicao
        AND m2.movimentacao = 'Atualização'
  )
ORDER BY a.ticker;
```

### Proventos received (dividends, JCP, FII income)

```sql
SELECT
    a.ticker,
    m.movimentacao AS tipo,
    m.data,
    m.quantidade,
    m.valor
FROM b3_movimentacoes m
JOIN ativos a ON a.id = m.ativo_id
WHERE m.movimentacao IN ('Dividendo', 'Juros Sobre Capital Próprio', 'Rendimento')
ORDER BY m.data DESC;
```

### Cost basis per asset (from notas)

```sql
SELECT
    a.ticker,
    n.tipo,
    SUM(n.valor_liquido) AS total_investido,
    SUM(n.taxas_proporcionais) AS total_taxas
FROM negociacoes n
JOIN ativos a ON a.id = n.ativo_id
WHERE n.tipo IN ('compra', 'aquisicao')
GROUP BY a.ticker, n.tipo
ORDER BY a.ticker;
```

### Assets requiring manual review

```sql
SELECT id, tipo, ticker, nome
FROM ativos
WHERE revisado = 0
ORDER BY tipo, ticker;
```

