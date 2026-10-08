# TODO

## Known Limitations

### Renda fixa ativo deduplication relies on consistent `titulo` strings

`ticker_aliases` maps the raw `titulo` text from a `TituloPrivado` invoice to a canonical `ativo_id`.
If the same instrument appears in two different invoices with slightly different text
(e.g. `"CDB PREFIXADO 14,30% AA"` vs `"CDB 14.30% PREFIXADO AA"`), the loader creates two
separate `ativos` rows for what is actually the same instrument.

**Mitigation in place:** The `ativos.revisado` flag marks auto-created rows as unreviewed.
A manual review pass can detect duplicates and merge them by re-pointing the relevant
`ticker_aliases` entries to the same `ativo_id` and updating `negociacoes.ativo_id` accordingly.

**Proper fix (future):** Normalize `titulo` strings at extraction time, or add a post-load
deduplication step that matches by `(cnpj_emissor, indexador, taxa_prefixada, percentual_do_indexador, emissao, vencimento)`.

### Ingestion still parses numbers as float

Calculations and reports use `Decimal` (NUMERIC is loaded as Decimal), but the
PDF extraction side — `parsers.py`, `models.py`, `transformer.py` (fee split) —
still turns document text into float before it is stored. (B3 reports already
parse straight to Decimal.)
It is harmless today (values have at most 2 decimals and the fee split rounds
to the cent; stored data checked on 2026-10-05), but parsing straight to
`Decimal` would make the pipeline exact end to end.

### A Nu Invest nota has a misextracted CPF

The key-value finder merged the client name and CPF; `cpf_cliente` got a value that
fails the CPF checksum. `cpf_parser` now validates check digits, so re-extracting that
PDF fails loudly instead of silently creating a phantom investidor. (The migrated copy
of this nota sits under the right investidor; the CPF is no longer stored.)

### Legacy B3 rows without an ativo

The legacy SQLite held B3 rows and aliases pointing at deleted ativos.
`migra_sqlite.py` kept those rows with `ativo_id = NULL`, matching what the old
reports showed. Re-attach them to an ativo if these positions should be tracked.

### IRPF assistant: codes to re-check every year, and what it does not cover

Every Receita code the app shows lives in `regras_fiscais.py` with its source.
The 2026 codes were read from the IRPF 2026 program itself (v1.5: the tables in
`lib/resources/tipoBens.xml`, `tipoRendIsento.xml`, `tipoRendTributExclusiva.xml`
and its `help/AjudaIRPF.pdf`). Each year, download the new program from
downloadirpf.receita.fazenda.gov.br and compare those tables. Rebuild
`dados/cnpjs.csv` (`cnpjs.py`) at the same time.

Open points on `/impostos/irpf`:

- **Units** are declared as 03-01 (shares); the table does not name units
  (`BEM_UNITS.verificado` is False).
- **FIP, FIDC and unrecognized funds** get no code: 07-06, 07-07 and 07-10 all
  fit depending on the fund. The page says "confira".
- **CNPJ gaps**: ETFs, infrastructure funds and FIDCs (CVM's reports for them
  carry no ISIN), and FIIs whose ISIN changed or that stopped reporting. An
  admin can set `ativos.cnpj_emissor`, which takes precedence.
- **Fixed income and Tesouro** appear in Bens e Direitos at the principal still
  applied (04-02 taxed, 04-03 exempt, by the bond's kind; debentures get no
  code). Their income (Exclusiva line 06, Isentos line 12) is not computed: the
  tax withheld at source is not in the documents, so the page points to the
  broker's informe. Tesouro has no issuer CNPJ on record.
- **Not covered**: subscription rights and receipts, bonus shares (line 18), BDR
  dividends (taxable), ETF and other funds' distributions, and the month-by-month
  Renda Variável sheet (the page links to the monthly tax page instead).
- **Line 20** sums the gains of exempt months; it does not net a month's losses
  against other months, matching the program's per-month sheet. Confirm.

### Monthly tax: open points

`apuracao.py` applies the rules in `regras_fiscais.py`. Still to settle:

- **Units**: taxed without the R$ 20 mil exemption per Solução de Consulta COSIT
  145/2021, seen only through a secondary source; read the primary text
  (`UNITS_SEM_ISENCAO.verificado` is False and the page says so).
- **Catalog classification** comes from `dados/classes_b3.csv` (`classes_b3.py`).
  Delisted tickers are known only through COTAHIST, which does not tell ETFs
  from other funds: a delisted ETF lands as `fundo` (left out of the DARF, with
  a notice) until an admin retypes it. Rebuild the table now and then so new
  listings are covered; a ticker missing from it falls back to the suffix
  (11 → FII).
- **Fiagro losses** are pooled with FII losses (`COMPENSACAO_FIAGRO_COM_FII`):
  confirm.
- **Fixed-income ETFs and infrastructure funds / FIP-IE / FIDC** (tipo `fundo`,
  or `etf` with subtipo `renda_fixa`) are kept in positions but not taxed:
  their tax is withheld at source or zero for individuals. The legacy CLI
  report (`imposto.py`) still applies 15% to every ETF.
- **Subscription rights** sold in bolsa are treated as regular operations without
  exemption until confirmed (`DIREITOS_SEM_ISENCAO`).
- **IRRF 0,005% left over** is carried only within the calendar year; confirm.
- **31/12** is treated as a non-business day for DARF due dates (banks closed);
  confirm against Receita's calendar.
- **Day trade** is separated from the position (q.705) but not taxed (20%, own
  loss pool, 1% IRRF): months with day trade are flagged instead.
- Whether day-trade sales count toward the R$ 20 mil limit: currently they do not.
- **Incorporação / conversão** (`CONVERSAO_CUSTO_TRANSFERIDO`): the new shares
  take the old ones' cost, no sale. For FII → FII this follows Lei 14.754/2023
  art. 30 §2º, read only through a secondary source (Planalto was unreachable);
  confirm the primary text. For stocks (incorporação de ações) the Receita's
  view is disputed: confirm. Cash paid in the event (a Resgate of the old
  quotas, a redemption of a temporary class) is not taxed or offset against the cost yet.
- **One-to-many conversions** (one class into two new ones): the whole cost goes to
  the ativo the user picks; the other credit stays at zero cost.

### Nu Invest "padrão de mercado" nota layout is not read

Since 2026 Nu offers two downloads of the same notas: its own layout ("Número
da nota", read by `extractors/nu_invest.py`) and the market-standard Sinacor
layout ("Nr. Nota / Folha / Data pregão"). The second raises
`LayoutNaoSuportado` with a message asking for the other model. Other brokers
use the Sinacor layout too, so an extractor for it would be reusable.
There is no real-PDF regression fixture for any layout yet: a sanitized
sample would still carry real trades, so it needs the owner's consent.

### Import worker wakes only in its own process

Uploads wake the worker thread of the process that received them. That covers one
machine (any number of uvicorn workers drain the shared queue, claimed with SKIP
LOCKED). With several machines, wake the others with Postgres LISTEN/NOTIFY.

### Password reset needs an e-mail provider

There is no "forgot password" flow yet; `admin.py definir-senha` is the only way to
reset a password. Needs a transactional e-mail provider before launch (also for e-mail
verification at sign-up).

### Account lockout can be triggered by anyone

Failed logins are limited per e-mail (5, then a 15-minute lock) and per client
IP (20 per 15 minutes, any e-mail; `Fly-Client-IP` on Fly). The per-account
lock still lets anyone who knows an e-mail keep that account locked out, from
many addresses. Options: a growing delay instead of a hard lock, or letting a
device that logged in before skip the lock (needs a device cookie).

### The parsing child still has network access

isolamento.py starts the child with a scrubbed environment (no DATABASE_URL or
CPF_HMAC_KEY), exchanges tagged JSON instead of pickle (the parent rebuilds only
the parse dataclasses it allows), and makes the web process non-dumpable so the
child cannot read its /proc environ or memory. What is left: the child runs as
the same user and can open network connections, so code execution through a
parser bug could still read files that user can read and talk to the outside.
To harden further: a separate user (needs root to switch), a network namespace
or seccomp filter, if the platform allows it.

### Options are stored but not calculated

Option trades from notas (market "OPCAO DE COMPRA/VENDA") are loaded under
ativos of tipo `opcao`, without a ticker (B3 reuses option codes; the name
carries the expiry). Positions, average cost and the monthly tax leave them
out, and the Posições and Impostos pages say so. Option exercises are already
loaded as trades of the underlying shares at the strike. Full support needs:
short positions, expiry without an event in the documents (worthless
options), the tax treatment of option results, and day trade.
