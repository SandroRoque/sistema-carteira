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

### Average cost ignores trade order

`posicoes.py`, `fechamento.py` and `imposto.py` compute average cost as
`SUM(all purchases) / SUM(all purchased qty)`. After a position is closed and
reopened, old purchases still weigh in: buy 100 @ 10, sell 100, buy 100 @ 20
reports R$ 15 instead of R$ 20. Pinned by the strict-xfail test
`tests/test_posicoes.py::test_custo_medio_reinicia_apos_zerar_posicao`.

**Fix:** replay events chronologically per ativo (buys, sells, bonificações,
desdobros) and move the arithmetic to `Decimal` at the same time — NUMERIC is
currently loaded as float (see `database.py`).

### A Nu Invest nota has a misextracted CPF

The key-value finder merged the client name and CPF; `cpf_cliente` got a value that
fails the CPF checksum. `cpf_parser` now validates check digits, so re-extracting that
PDF fails loudly instead of silently creating a phantom investidor.

### Legacy B3 rows without an ativo

The legacy SQLite held B3 rows and aliases pointing at deleted ativos.
`migra_sqlite.py` kept those rows with `ativo_id = NULL`, matching what the old
reports showed. Re-attach them to an ativo if these positions should be tracked.

### Catalog edits are not yet restricted

`ativos` is shared by every investidor; `PATCH /ativos/{id}` and `revisa_ativos.py`
must become admin-only once authentication exists.
