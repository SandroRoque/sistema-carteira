-- Schema of the legacy single-user SQLite database (before the PostgreSQL migration).
-- Used by tests/test_migra_sqlite.py.
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS ativos (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo                    TEXT    NOT NULL DEFAULT 'desconhecido',
    subtipo                 TEXT,
    ticker                  TEXT    UNIQUE,
    nome                    TEXT,
    cnpj_emissor            TEXT,
    emissor                 TEXT,
    indexador               TEXT,
    taxa_prefixada          REAL,
    percentual_do_indexador REAL,
    emissao                 TEXT,
    vencimento              TEXT,
    revisado                INTEGER NOT NULL DEFAULT 0
);

-- Maps the raw text found in PDFs to a canonical ativo.
-- Same raw_text always resolves to the same ativo_id.
-- Users can re-point aliases to a different ativo_id during review.
CREATE TABLE IF NOT EXISTS ticker_aliases (
    raw_text TEXT    PRIMARY KEY,
    ativo_id INTEGER NOT NULL REFERENCES ativos(id)
);

CREATE TABLE IF NOT EXISTS notas (
    nota_id         TEXT NOT NULL,
    corretora_id    TEXT NOT NULL,
    doc_type        TEXT NOT NULL,
    data_pregao     TEXT NOT NULL,
    data_de_liquidacao TEXT,
    cpf_cliente     TEXT NOT NULL,
    codigo_cliente  TEXT NOT NULL,
    nome_cliente    TEXT,
    assessor        TEXT,
    folha           TEXT,
    endereco        TEXT,
    cidade          TEXT,
    uf              TEXT,
    cep             TEXT,
    nota_de         TEXT,
    local           TEXT,
    emissor         TEXT,
    cnpj_emissor    TEXT,
    comando         TEXT,
    mercado         TEXT,
    status          TEXT,
    liquido_para    REAL,
    -- resumo dos negócios (NotaCorretagem only)
    debentures      REAL,
    vendas_a_vista  REAL,
    compras_a_vista REAL,
    opcoes_compras  REAL,
    opcoes_vendas   REAL,
    operacoes_a_termo REAL,
    valor_das_operacoes_com_titulos_publicos REAL,
    valor_das_operacoes REAL,
    valor_liquido_das_operacoes REAL,
    -- resumo financeiro (NotaCorretagem only)
    taxa_de_liquidacao REAL,
    taxa_de_registro REAL,
    total_clearing_cblc REAL,
    taxa_de_termo_opcoes REAL,
    taxa_a_n_a REAL,
    emolumentos REAL,
    total_bolsa REAL,
    corretagem REAL,
    iss REAL,
    irrf_sobre_operacoes REAL,
    outras REAL,
    total_corretagem_despesas REAL,
    taxa_operacional REAL,
    execucao REAL,
    taxa_de_custodia REAL,
    impostos REAL,
    pis_cofins REAL,
    taxa_de_transferencia_de_ativos REAL,
    execucao_casa REAL,
    filename        TEXT NOT NULL,
    processado_em   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (nota_id, corretora_id, doc_type)
);

CREATE TABLE IF NOT EXISTS negociacoes (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    nota_id                  TEXT    NOT NULL,
    corretora_id             TEXT    NOT NULL,
    doc_type                 TEXT    NOT NULL,
    linha_na_nota            INTEGER NOT NULL DEFAULT 0,
    ativo_id                 INTEGER NOT NULL REFERENCES ativos(id),
    data                     TEXT    NOT NULL,
    sentido                  TEXT    NOT NULL,
    tipo                     TEXT    NOT NULL,
    debito_credito           TEXT,
    quantidade               REAL,
    preco_unitario           REAL,
    valor_bruto              REAL,
    taxas_proporcionais      REAL    NOT NULL DEFAULT 0,
    valor_liquido            REAL,
    mercado                  TEXT,
    tipo_de_mercado          TEXT,
    prazo                    TEXT,
    observacao               TEXT,
    indexador                TEXT,
    taxa_cupom_percentual    REAL,
    percentual_do_indexador  REAL,
    emissao                  TEXT,
    vencimento               TEXT,
    custodia                 TEXT,
    tipo_emitente            TEXT,
    conta_bancaria           TEXT,
    rendimentos              TEXT,
    imposto_de_renda_federal REAL,
    iof                      REAL,
    especificacao_observacao TEXT,
    tx_bvmf                  REAL,
    tx_agente_custodia       REAL,
    FOREIGN KEY (nota_id, corretora_id, doc_type) REFERENCES notas(nota_id, corretora_id, doc_type),
    UNIQUE (nota_id, corretora_id, doc_type, linha_na_nota)
);

CREATE TABLE IF NOT EXISTS b3_arquivos_processados (
    arquivo       TEXT NOT NULL PRIMARY KEY,
    processado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Raw rows from B3 movimentações Excel reports.
-- One row per Excel line; ativo_id is NULL when the product can't be resolved.
CREATE TABLE IF NOT EXISTS b3_movimentacoes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sentido        TEXT    NOT NULL,
    data           TEXT    NOT NULL,
    movimentacao   TEXT    NOT NULL,
    produto_raw    TEXT    NOT NULL,
    ativo_id       INTEGER REFERENCES ativos(id),
    instituicao    TEXT,
    quantidade     REAL,
    preco_unitario REAL,
    valor          REAL,
    arquivo        TEXT    NOT NULL REFERENCES b3_arquivos_processados(arquivo)
);

-- Cost basis for bonus shares (Bonificação em Ativos).
-- One row per B3 movimentação of type "Bonificação em Ativos".
-- custo_por_cota is NULL until the user informs the acquisition cost declared
-- by the company (needed for correct average-cost calculation).
CREATE TABLE IF NOT EXISTS bonificacoes (
    b3_movimentacao_id INTEGER PRIMARY KEY
        REFERENCES b3_movimentacoes(id) ON DELETE CASCADE,
    custo_por_cota     REAL  -- NULL = cost unknown / not yet informed
);

