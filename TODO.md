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

### Calculations use float

NUMERIC is loaded as float (see `database.py`), so positions, average cost and
taxes are computed in binary floating point. Move the arithmetic to `Decimal`.

### A Nu Invest nota has a misextracted CPF

The key-value finder merged the client name and CPF; `cpf_cliente` got a value that
fails the CPF checksum. `cpf_parser` now validates check digits, so re-extracting that
PDF fails loudly instead of silently creating a phantom investidor. (The migrated copy
of this nota sits under the right investidor; the CPF is no longer stored.)

### Legacy B3 rows without an ativo

The legacy SQLite held B3 rows and aliases pointing at deleted ativos.
`migra_sqlite.py` kept those rows with `ativo_id = NULL`, matching what the old
reports showed. Re-attach them to an ativo if these positions should be tracked.

### Password reset needs an e-mail provider

There is no "forgot password" flow yet; `admin.py definir-senha` is the only way to
reset a password. Needs a transactional e-mail provider before launch (also for e-mail
verification at sign-up).

### Login throttling is per account only

Lockout counts failures per e-mail. An attacker spraying one password across many
e-mails is not throttled; add per-IP rate limiting at the edge (proxy/CDN) or in the app.
