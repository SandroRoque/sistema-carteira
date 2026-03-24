# Sistema Carteira

Backend de extração de notas e relatórios de corretoras em PDF.

Hoje o projeto já consegue extrair documentos textuais de:

- `nu_invest_nota_corretagem`
- `nu_invest_titulos_publicos`
- `nu_invest_titulos_privados`
- `xp_nota_corretagem`
- `safra_nota_corretagem`

## Visão Geral

O projeto está organizado em duas camadas principais:

- configuração declarativa de corretoras, campos e layouts
- funções de extração/parsing que usam essa configuração

Arquivos principais:

- [layout_config.py](/home/roque/Documents/Projects/sistema-carteira/layout_config.py): `CORRETORAS`, `FIELD_CONFIG` e `LAYOUT_CONFIG`
- [models.py](/home/roque/Documents/Projects/sistema-carteira/models.py): modelos leves, como `Corretora`
- [parsers.py](/home/roque/Documents/Projects/sistema-carteira/parsers.py): parsers de datas, números, percentuais, CPF e dinheiro
- [key_value_finders.py](/home/roque/Documents/Projects/sistema-carteira/key_value_finders.py): extração de grupos `KEY_VALUE`
- [movimentacoes_table_finder.py](/home/roque/Documents/Projects/sistema-carteira/movimentacoes_table_finder.py): extração de grupos `TABLE`
- [extrai_nota_de_negociacao.py](/home/roque/Documents/Projects/sistema-carteira/extrai_nota_de_negociacao.py): classe `NotaNegociacaoExtractor`
- [main.py](/home/roque/Documents/Projects/sistema-carteira/main.py): harness de prototipagem
- [exporta_csvs.py](/home/roque/Documents/Projects/sistema-carteira/exporta_csvs.py): exportação em CSV do acervo inteiro
- [settings.py](/home/roque/Documents/Projects/sistema-carteira/settings.py): carregamento centralizado de variáveis de ambiente

## Modelo de Configuração

`FIELD_CONFIG` define o catálogo canônico de campos:

- `id`
- `data_type`
- `required_default`
- `parser_default`

`LAYOUT_CONFIG` define cada layout por corretora:

- `id`
- `corretora_id`
- `groups`

Cada `group` define:

- `id`
- `type`: `KEY_VALUE` ou `TABLE`
- `anchors`
- `direction`: `RIGHT`, `BELOW` ou `None`
- `bindings`
- `options`

Cada `binding` conecta o campo canônico ao rótulo daquele layout:

- `field_id`
- `label`
- `options` opcionais, como `occurrence_index`

## Extração

Os grupos `KEY_VALUE` usam dois modos:

- `RIGHT`: valor à direita do rótulo
- `BELOW`: valor abaixo do rótulo

Os grupos `TABLE` usam âncoras superior e inferior para delimitar a tabela e depois
mapeiam os cabeçalhos detectados para `field_id`.

Os parsers são aplicados durante a orquestração, não dentro dos finders.

Comportamentos já suportados:

- valores monetários com `R$`
- sinal via sufixo `D` / `C`
- percentuais
- números brasileiros com vírgula decimal
- CPF normalizado
- valores multiline específicos em grupos verticais configurados

## Configuração de Ambiente

As configurações de runtime ficam fora do repositório e são lidas por
[settings.py](/home/roque/Documents/Projects/sistema-carteira/settings.py).

Use [.env.example](/home/roque/Documents/Projects/sistema-carteira/.env.example) como referência.

Variáveis atuais:

- `NOTAS_DIR`: diretório onde estão os PDFs a processar
- `PROTOTYPE_PDF_NAMES`: lista opcional usada pelo `main.py` durante prototipagem

## Execução

Rodar o harness de prototipagem:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python main.py
```

Exportar o acervo para CSV:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python exporta_csvs.py
```

Arquivos gerados:

- `exports/receipts.csv`
- `exports/movimentacoes.csv`

`receipts.csv` contém uma linha por arquivo, inclusive quando houver falha.

Colunas importantes:

- `receipt_id`
- `filename`
- `corretora_id`
- `layout_id`
- `status`
- `error`

`movimentacoes.csv` contém uma linha por movimentação e referencia o recibo por:

- `receipt_id`

## Testes

Rodar testes:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --group dev pytest -q
```

Cobertura atual:

- parsers
- sanidade da configuração

Arquivos de teste:

- [tests/test_parsers.py](/home/roque/Documents/Projects/sistema-carteira/tests/test_parsers.py)
- [tests/test_layout_config.py](/home/roque/Documents/Projects/sistema-carteira/tests/test_layout_config.py)

## Estado Atual

O código já está em um estado utilizável para extração em lote, mas ainda há pontos de evolução:

- `main.py` continua sendo um arquivo de prototipagem
- `NotaNegociacaoExtractor` ainda não concentra toda a orquestração final
- a identificação automática de layout ainda pode ser refinada mais dentro da classe
- PDFs sem texto extraível ainda dependem de uma estratégia futura de OCR
