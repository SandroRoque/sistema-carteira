# Sistema Carteira

Extrai notas de corretagem e outros documentos de corretoras em PDF, cruza com os
relatórios de movimentações da B3 e calcula posições, custo médio, proventos e
apuração de IR. Os dados ficam em PostgreSQL, separados por investidor.

Corretoras e tipos de documento suportados:

| Corretora | Documentos |
|---|---|
| Nu Invest | Nota de corretagem, Títulos públicos, Títulos privados |
| XP Investimentos | Nota de corretagem |
| Safra | Nota de corretagem |
| Brasil Plural | Nota de corretagem |

## Arquitetura

```
PDFs (NOTAS_DIR)
    └─► extrai_nota_de_negociacao.py   identifica corretora, chama extrator
            └─► extractors/<corretora>.py   extrai campos do PDF com PyMuPDF
    └─► transformer.py                 normaliza tipos, distribui taxas por operação
    └─► loader.py                      grava no Postgres, resolve ativos, idempotente

Relatórios B3 (xlsx)
    └─► carrega_b3.py                  proventos e eventos corporativos

PostgreSQL
    └─► posicoes.py / fechamento.py / imposto.py   cálculos
    └─► app/ (FastAPI + HTMX)  ·  portfolio.py / relatorio.py (CLI)
```

Relatórios de movimentações da B3 fornecem dividendos, JCP, rendimentos de FII e
eventos corporativos — ver [docs/b3-movimentacoes.md](docs/b3-movimentacoes.md).

### Multi-tenancy

A **conta** é dona de tudo o que envia; o sistema não verifica identidade. Dentro da
conta, as notas são separadas em **investidores** — uma carteira por CPF encontrado nas
notas, porque posição e IR são por pessoa. Toda consulta é filtrada por `investidor_id`.
O cadastro de **ativos** é compartilhado: PETR4 é o mesmo instrumento para todos.
Detalhes em [docs/schema.md](docs/schema.md).

### Dados pessoais

O CPF nunca é gravado: só um HMAC com segredo do servidor (para reconhecer o mesmo CPF
em envios futuros) e uma forma mascarada. Nome, endereço e código de cliente das notas
são descartados na extração. Inventário, retenção e direitos do titular em
[docs/lgpd.md](docs/lgpd.md).

## Arquivos principais

| Arquivo | Responsabilidade |
|---|---|
| `tabelas.py` | Schema (SQLAlchemy Core) — fonte da verdade das migrações |
| `migrations/` | Migrações Alembic |
| `database.py` | Engine, `connect()` transacional e helpers de consulta |
| `contas.py` | Usuários e investidores (fronteira de tenancy) |
| `carrega_notas.py` | Entry point: itera PDFs, extrai, transforma e carrega |
| `carrega_b3.py` | Carrega relatórios de movimentações da B3 |
| `loader.py` | Escrita: resolução de ativos, idempotência por nota |
| `posicoes.py` | Posição atual por ativo |
| `fechamento.py` / `imposto.py` / `relatorio.py` | Fechamento anual, IR e relatório Markdown |
| `app/` | Interface web (FastAPI, Jinja, HTMX) |
| `extrai_nota_de_negociacao.py` / `extractors/` | Extração de PDFs por corretora |
| `transformer.py` | Conversão pura: dataclasses → registros normalizados + rateio de taxas |
| `parsers.py` | Parsers de datas, números BR, percentuais, CPF (com dígitos verificadores) |
| `migra_sqlite.py` | Importação única do banco SQLite da versão single-user |

## Desenvolvimento local

Requisitos: [uv](https://docs.astral.sh/uv/) e Docker.

```bash
uv sync --dev
cp .env.example .env              # ajuste NOTAS_DIR e gere CPF_HMAC_KEY
docker compose up -d              # Postgres em localhost:5433
uv run alembic upgrade head       # cria/atualiza o schema
uv run uvicorn app.main:app --reload
```

Carregar dados:

```bash
CARTEIRA_USUARIO_EMAIL=voce@exemplo.com uv run python carrega_notas.py
uv run python carrega_b3.py       # usa o único investidor, ou CARTEIRA_INVESTIDOR_ID
```

Relatórios de linha de comando (`portfolio.py`, `imposto.py`, `fechamento.py`,
`relatorio.py`, `reconcilia.py`) usam o investidor de `CARTEIRA_INVESTIDOR_ID`, ou o
único cadastrado.

### Vindo da versão SQLite

```bash
uv run python migra_sqlite.py --email voce@exemplo.com --sqlite carteira.db
```

O script confere as contagens de cada tabela e avisa sobre referências órfãs.

## Migrações

Altere `tabelas.py` e gere a migração:

```bash
uv run alembic revision --autogenerate -m "descrição"
uv run alembic upgrade head
uv run alembic check              # falha se o schema e tabelas.py divergirem
```

Revise sempre o arquivo gerado antes de commitar.

## Testes

```bash
uv run pytest -q
```

Os testes sobem um PostgreSQL descartável via [testcontainers](https://testcontainers.com/)
(é preciso Docker), aplicam as migrações reais e cobrem parsers, carga, isolamento entre
investidores, cálculos de posição, relatórios, rotas web e a migração do SQLite.
