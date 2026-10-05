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
