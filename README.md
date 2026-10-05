# Sistema Carteira

Extrai notas de corretagem e outros documentos de corretoras em PDF e os carrega
em um banco de dados SQLite local para rastreamento de carteira pessoal.

Corretoras e tipos de documento suportados:

| Corretora | Documentos |
|---|---|
| Nu Invest | Nota de corretagem, Títulos públicos, Títulos privados |
| XP Investimentos | Nota de corretagem |
| Safra | Nota de corretagem |
| Brasil Plural | Nota de corretagem |

## Arquitetura

O pipeline tem três etapas:

```
PDFs (notas/)
    └─► extrai_nota_de_negociacao.py   identifica corretora, chama extrator
            └─► extractors/<corretora>.py   extrai campos do PDF com PyMuPDF
    └─► transformer.py                 normaliza tipos, distribui taxas por operação
    └─► loader.py                      grava no SQLite, resolve ativos, idempotente
            └─► carteira.db
```

Além dos PDFs, relatórios de movimentações exportados da B3 (xlsx) fornecem
dividendos, JCP, rendimentos de FII e eventos corporativos — ver
[docs/b3-movimentacoes.md](docs/b3-movimentacoes.md).

## Arquivos principais

| Arquivo | Responsabilidade |
|---|---|
| `carrega_notas.py` | Entry point: itera PDFs, extrai, transforma e carrega no DB |
| `exporta_csvs.py` | Exporta o acervo de PDFs para `exports/extracao_notas.xlsx` (diagnóstico) |
| `extrai_nota_de_negociacao.py` | Dispatcher: identifica corretora e chama o extrator certo |
| `extractors/` | Um módulo por corretora, cada um expõe `extract(page)` |
| `transformer.py` | Conversão pura: dataclasses → registros normalizados + distribuição de taxas |
| `loader.py` | Escrita no SQLite: resolução de ativos, idempotência via `notas_processadas` |
| `database.py` | Schema SQLite e context manager `connect()` |
| `models.py` | Dataclasses de domínio: `Corretora`, `NotaCorretagem`, `TituloPublico`, `TituloPrivado`, `Movimentacao` |
| `parsers.py` | Parsers de datas, números BR, percentuais, CPF e valores monetários |
| `key_value_finders.py` | Extração de pares chave-valor de páginas PDF |
| `movimentacoes_table_finder.py` | Extração da tabela de movimentações de notas de corretagem |
| `settings.py` | Carregamento centralizado de variáveis de ambiente |

## Configuração de ambiente

Copie `.env.example` para `.env` e preencha:

```bash
cp .env.example .env
```

Variáveis:

- `NOTAS_DIR` — diretório com os PDFs a processar (obrigatório)
- `CARTEIRA_DB` — caminho do banco SQLite (padrão: `carteira.db` na raiz do projeto)

## Execução

Carregar todos os PDFs no banco:

```bash
.venv/bin/python carrega_notas.py
```

Exportar acervo para Excel (diagnóstico / conferência):

```bash
.venv/bin/python exporta_csvs.py
```

Gera `exports/extracao_notas.xlsx` com duas abas:

- `notas` — uma linha por documento, com todos os campos financeiros
- `movimentacoes` — uma linha por operação de compra/venda

## Banco de dados

Schema em `database.py`. Tabelas principais:

| Tabela | Conteúdo |
|---|---|
| `ativos` | Cadastro de ativos; novos ativos são criados automaticamente com `revisado=0` |
| `ticker_aliases` | Mapeia o texto bruto do PDF para `ativo_id` |
| `operacoes` | Uma linha por compra/venda, com taxas proporcionais alocadas |
| `custos_de_nota` | Breakdown completo de taxas de cada nota de corretagem |
| `notas_processadas` | Controle de idempotência — impede reinserção do mesmo PDF |

Após carregar, revisar ativos não identificados:

```sql
SELECT * FROM ativos WHERE revisado = 0 ORDER BY nome;
```

## Testes

```bash
.venv/bin/python -m pytest -q
```

Cobertura atual: `tests/test_parsers.py`
