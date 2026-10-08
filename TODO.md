# TODO

## Known Limitations

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
- **Subscription rights and receipts** are declared as 04-04 (traded in bolsa,
  neither shares nor funds); the table does not name them (`BEM_DIREITOS`).
- **BDR dividends** are listed month by month as taxable (carnê-leão). No
  official text says so: the PR-IRPF-2026 and the program's help are silent on
  BDRs; B3 and brokers say carnê-leão at the progressive table
  (`DIVIDENDOS_BDR_CARNE_LEAO`). The amounts are what B3 credited, net of the
  tax withheld abroad; the carnê-leão wants the gross amount.
- **Renda Variável sheet**: BDRs, ETFs, units and rights go in "Mercado à vista
  - ações", the only spot line the sheet has (`RV_OUTROS_A_VISTA`). The 0,005%
  IRRF of a month is shown whole in the stocks' sheet even when part of it came
  from FII sales.
- **Bonus shares** (line 18) need the cost per share the company announced
  (entered on Pendências).
- **Not covered**: ETF and other funds' distributions.
- **Line 20** sums the gains of exempt months; it does not net a month's losses
  against other months, matching the program's per-month sheet. Confirm.
- **Dividends from 2026 on**: Lei 15.270/2025 withholds 10% on dividends above
  R$ 50 mil a month from one company (PR-IRPF-2026 p.144). The 2027
  declaration will need it; line 09 is still right for 2025.

### Monthly tax: open points

`apuracao.py` applies the rules in `regras_fiscais.py`. Still to settle:

- **Units**: taxed without the R$ 20 mil exemption, attributed to Solução de
  Consulta COSIT 145/2021 (`UNITS_SEM_ISENCAO.verificado` is False and the page
  says so). The official text is at
  normas.receita.fazenda.gov.br/sijut2consulta/anexoOutros.action?idArquivoBinario=62757
  but only opens in a browser (it failed with curl and WebFetch on 2026-10-08).
  Secondary summaries say SC 145/2021 is about ETF quotas and subscription
  warrants: the exemption covers only "ativos da espécie ações" (IN-1585 art.
  59, I). The link to units (certificados de depósito de ações) comes from
  commentators, not from a quoted passage. Read the PDF: if it does not name
  units, find the act that does, or decide on the reading.
- **Catalog classification** comes from `dados/classes_b3.csv` (`classes_b3.py`).
  Delisted tickers are known only through COTAHIST, which does not tell ETFs
  from other funds: a delisted ETF lands as `fundo` (left out of the DARF, with
  a notice) until an admin retypes it. Rebuild the table now and then so new
  listings are covered; a ticker missing from it falls back to the suffix
  (11 → FII).
- **Fiagro losses** are pooled with FII losses (`COMPENSACAO_FIAGRO_COM_FII`):
  confirm (PR-IRPF-2026 says nothing on it).
- **Fixed-income ETFs and infrastructure funds / FIP-IE / FIDC** (tipo `fundo`,
  or `etf` with subtipo `renda_fixa`) are kept in positions but not taxed:
  their tax is withheld at source or zero for individuals.
- **Subscription rights** sold in bolsa are treated as regular operations without
  exemption until confirmed (`DIREITOS_SEM_ISENCAO`).
- **IRRF 0,005% left over** is carried only within the calendar year; confirm.
  PR-IRPF-2026 q.706 only says it is deducted from the monthly tax; the
  same-year limit is explicit for day trade (q.715), not for the 0,005%.
- **31/12** is treated as a non-business day for DARF due dates (banks closed);
  confirm against Receita's calendar.
- **Day trade** is taxed (20%, no exemption, own loss pool; PR-IRPF-2026
  q.705-715). The 1% the broker withholds is read from market-standard notas
  ("IRRF Day-Trade: Base ... Projeção ..."; `notas.irrf_day_trade`), printed
  apart from the 0,005%. Where no nota of the day prints it (Nu's own layout,
  notas loaded before migration 0014), it is estimated as 1% of the day's net
  day-trade result per broker, and the page says so. Re-import old notas to
  replace the estimate. The printed value is a "projeção": confirm it matches
  what the broker actually withheld (the informe de rendimentos).
- Whether day-trade sales count toward the R$ 20 mil limit: currently they do not.
  PR-IRPF-2026 q.707 only says the exemption does not apply to day trade.
- **Incorporação / conversão** (`CONVERSAO_CUSTO_TRANSFERIDO`): the new shares
  take the old ones' cost, no sale. For FII → FII there is no source yet: Lei
  14.754/2023 art. 30, §2º (no IRRF when funds of the same regime merge) does
  not apply, because its art. 39, I leaves FIIs and Fiagros out ("Ficam
  ressalvadas do disposto nesta Lei as regras aplicáveis aos [...] FII e [...]
  Fiagro"; Planalto, read 2026-10-08). Look for a Solução de Consulta on FII
  mergers. For stocks (incorporação de ações) the Receita's
  view is that the transfer is a sale: PR-IRPF-2026 q.603 ("a transferência
  destas para o capital social da companhia incorporadora caracteriza alienação
  cujo valor, se superior ao indicado na declaração de bens [...] é tributado
  pela diferença a maior, como ganho de capital"). That is ganho de capital
  (GCAP), outside the monthly bolsa calculation, and courts have disagreed. The
  app still carries the cost over with no gain: decide whether to flag stock
  conversions as a possible GCAP event. A merger of companies (incorporação de
  sociedade) is a different event. Cash paid in the event (a Resgate of the old
  quotas, a redemption of a temporary class) is not taxed or offset against the cost yet.
- **One-to-many conversions** (one class into two new ones): the whole cost goes to
  the ativo the user picks; the other credit stays at zero cost.

### Nu Invest "padrão de mercado" layout: not checked on a real nota

Nu offers its notas in its own layout ("Número da nota", `extractors/nu_invest.py`)
and in the market-standard Sinacor one ("Nr. Nota / Folha / Data pregão"), which
now goes to `extractors/sinacor.py`. It is tested only on a synthetic PDF: check
it on a real one. Also, the two layouts give the same nota different ids (Sinacor
ids carry the date and a trade fingerprint), so sending the same nota in both
layouts loads its trades twice. The B3 statement check on /importar then shows
them as quantities that differ from the B3.
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

### Login waits: what is left

Failed logins are limited per IP (20 per 15 minutes, any e-mail) and per
account: from the 5th failure in a row the account waits 30 s, doubling up to
15 min. A browser that logged in to the account before (signed `dispositivo`
cookie) skips the account's wait, so someone who only knows the e-mail cannot
keep its owner out of a device already used. A new device can still be kept
waiting. A device cookie cannot be revoked: it is an HMAC under CPF_HMAC_KEY,
which must not rotate. It only skips the wait and never logs anyone in; if
revocation matters, store device tokens in a table instead.

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
