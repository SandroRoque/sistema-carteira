from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from models import Movimentacao, NotaCorretagem, TituloPrivado, TituloPublico

ZERO = Decimal(0)


@dataclass
class NotaRecord:
    nota_id: str
    corretora_id: str
    doc_type: str
    data_pregao: date
    data_de_liquidacao: date | None
    cpf_cliente: str | None
    codigo_cliente: str
    nome_cliente: str | None
    assessor: str | None
    folha: str | None
    endereco: str | None
    cidade: str | None
    uf: str | None
    cep: str | None
    nota_de: str | None
    local: str | None
    emissor: str | None
    cnpj_emissor: str | None
    comando: str | None
    mercado: str | None
    status: str | None
    liquido_para: Decimal | None
    # resumo dos negócios (NotaCorretagem only)
    debentures: Decimal | None
    vendas_a_vista: Decimal | None
    compras_a_vista: Decimal | None
    opcoes_compras: Decimal | None
    opcoes_vendas: Decimal | None
    operacoes_a_termo: Decimal | None
    valor_das_operacoes_com_titulos_publicos: Decimal | None
    valor_das_operacoes: Decimal | None
    valor_liquido_das_operacoes: Decimal | None
    # resumo financeiro (NotaCorretagem only)
    taxa_de_liquidacao: Decimal | None
    taxa_de_registro: Decimal | None
    total_clearing_cblc: Decimal | None
    taxa_de_termo_opcoes: Decimal | None
    taxa_a_n_a: Decimal | None
    emolumentos: Decimal | None
    total_bolsa: Decimal | None
    corretagem: Decimal | None
    iss: Decimal | None
    irrf_sobre_operacoes: Decimal | None
    irrf_day_trade: Decimal | None
    outras: Decimal | None
    total_corretagem_despesas: Decimal | None
    taxa_operacional: Decimal | None
    execucao: Decimal | None
    taxa_de_custodia: Decimal | None
    impostos: Decimal | None
    pis_cofins: Decimal | None
    taxa_de_transferencia_de_ativos: Decimal | None
    execucao_casa: Decimal | None


@dataclass
class NegociacaoRecord:
    nota_id: str
    corretora_id: str
    doc_type: str
    linha_na_nota: int
    # raw_ticker is resolved to ativo_id at load time — not stored in the DB
    raw_ticker: str
    data: date
    sentido: str  # 'entrada' | 'saida'
    tipo: str     # 'compra' | 'venda' | 'aquisicao' | 'resgate'
    debito_credito: str | None
    quantidade: Decimal | None
    preco_unitario: Decimal | None
    valor_bruto: Decimal | None
    taxas_proporcionais: Decimal
    valor_liquido: Decimal | None
    mercado: str | None
    tipo_de_mercado: str | None
    prazo: str | None
    observacao: str | None
    indexador: str | None
    taxa_cupom_percentual: Decimal | None
    percentual_do_indexador: Decimal | None
    emissao: date | None
    vencimento: date | None
    custodia: str | None
    tipo_emitente: str | None
    conta_bancaria: str | None
    rendimentos: str | None
    imposto_de_renda_federal: Decimal | None
    iof: Decimal | None
    especificacao_observacao: str | None
    tx_bvmf: Decimal | None
    tx_agente_custodia: Decimal | None


@dataclass
class DocumentoTransformado:
    nota: NotaRecord
    negociacoes: list[NegociacaoRecord]


# ---------------------------------------------------------------------------
# Fee distribution
# ---------------------------------------------------------------------------

def _distribuir_taxas(valores_brutos: list[Decimal], total_taxas: Decimal) -> list[Decimal]:
    """Distribui total_taxas proporcionalmente aos valores_brutos (abs).

    Usa o método 'maior resto' para corrigir erros de arredondamento:
    a operação de maior valor absorve qualquer centavo residual.
    """
    n = len(valores_brutos)
    if n == 0 or total_taxas == 0:
        return [ZERO] * n

    abs_valores = [abs(v) for v in valores_brutos]
    total_abs = sum(abs_valores)

    if total_abs == 0:
        # Fallback: distribute equally when all trade values are zero.
        each = round(total_taxas / n, 2)
        allocated = [each] * n
        allocated[0] = round(allocated[0] + round(total_taxas - sum(allocated), 2), 2)
        return allocated

    allocated = [round(total_taxas * v / total_abs, 2) for v in abs_valores]
    remainder = round(total_taxas - sum(allocated), 2)
    if remainder != 0:
        largest_idx = max(range(n), key=lambda i: abs_valores[i])
        allocated[largest_idx] = round(allocated[largest_idx] + remainder, 2)

    return allocated


def _total_taxas_nota(nota: NotaCorretagem) -> Decimal:
    """Total broker fees for a nota.

    Computed as the difference between the net trade value and what the
    client actually pays/receives.  This is broker-agnostic and avoids
    double-counting subtotal columns.
    """
    liq_ops = nota.valor_liquido_das_operacoes or ZERO
    liq_para = nota.liquido_para or ZERO
    # For a net buyer: liq_ops < 0, liq_para < liq_ops  → result > 0
    # For a net seller: liq_ops > 0, liq_para < liq_ops → result > 0
    return round(liq_ops - liq_para, 2)


# ---------------------------------------------------------------------------
# Tipo and sentido normalization
# ---------------------------------------------------------------------------

def _tipo_nota_corretagem(compra_venda: str) -> str:
    cv = compra_venda.strip().upper()
    if cv == "C":
        return "compra"
    if cv == "V":
        return "venda"
    return cv.lower()


def _tipo_titulo_publico(tipo: str) -> str:
    t = tipo.strip().upper()
    if t in ("COMPRA", "C"):
        return "compra"
    if t in ("VENDA", "V"):
        return "venda"
    return t.lower()


def _tipo_titulo_privado(nota_de: str) -> str:
    n = nota_de.strip().upper()
    if "RESGATE" in n:
        return "resgate"
    if "APLICA" in n:
        return "aquisicao"
    # "VENDA FINAL" is Nu Invest's label for a fixed-income purchase:
    # the issuer "sells" the bond to the investor.  Must be checked before
    # the generic "VENDA" branch below.
    if "VENDA FINAL" in n:
        return "aquisicao"
    if "COMPRA" in n:
        return "compra"
    if "VENDA" in n:
        return "venda"
    return n.lower()


def _sentido(tipo: str) -> str:
    return "entrada" if tipo in ("compra", "aquisicao") else "saida"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def transformar(resultado: NotaCorretagem | TituloPublico | TituloPrivado) -> DocumentoTransformado:
    if isinstance(resultado, NotaCorretagem):
        return _transformar_nota_corretagem(resultado)
    if isinstance(resultado, TituloPublico):
        return _transformar_titulo_publico(resultado)
    if isinstance(resultado, TituloPrivado):
        return _transformar_titulo_privado(resultado)
    raise TypeError(f"Tipo não suportado: {type(resultado)}")


# ---------------------------------------------------------------------------
# Per-type transformers
# ---------------------------------------------------------------------------

def _transformar_nota_corretagem(nota: NotaCorretagem) -> DocumentoTransformado:
    movs = nota.movimentacoes
    valores_brutos = [m.valor_ajuste or ZERO for m in movs]
    total_taxas = _total_taxas_nota(nota)
    taxas_por_mov = _distribuir_taxas(valores_brutos, total_taxas)

    negociacoes = []
    for i, (mov, taxa) in enumerate(zip(movs, taxas_por_mov)):
        tipo = _tipo_nota_corretagem(mov.compra_venda)
        valor_bruto = mov.valor_ajuste or ZERO
        # Buys: fees increase cost.  Sells: fees reduce proceeds.
        sinal = 1 if tipo == "compra" else -1
        valor_liquido = round(valor_bruto + sinal * taxa, 2)

        negociacoes.append(NegociacaoRecord(
            nota_id=nota.numero_da_nota,
            corretora_id=nota.corretora_id,
            doc_type="NotaCorretagem",
            linha_na_nota=i,
            raw_ticker=mov.especificacao_do_titulo,
            data=nota.data_pregao,
            sentido=_sentido(tipo),
            tipo=tipo,
            debito_credito=mov.debito_credito,
            quantidade=mov.quantidade,
            preco_unitario=mov.preco_ajuste,
            valor_bruto=valor_bruto,
            taxas_proporcionais=taxa,
            valor_liquido=valor_liquido,
            mercado=mov.mercado,
            tipo_de_mercado=mov.tipo_de_mercado,
            prazo=mov.prazo,
            observacao=mov.observacao,
            indexador=None,
            taxa_cupom_percentual=None,
            percentual_do_indexador=None,
            emissao=None,
            vencimento=None,
            custodia=None,
            tipo_emitente=None,
            conta_bancaria=None,
            rendimentos=None,
            imposto_de_renda_federal=None,
            iof=None,
            especificacao_observacao=None,
            tx_bvmf=None,
            tx_agente_custodia=None,
        ))

    nota_record = NotaRecord(
        nota_id=nota.numero_da_nota,
        corretora_id=nota.corretora_id,
        doc_type="NotaCorretagem",
        data_pregao=nota.data_pregao,
        data_de_liquidacao=None,
        cpf_cliente=nota.cpf_cliente,
        codigo_cliente=nota.codigo_cliente,
        nome_cliente=nota.nome_cliente,
        assessor=nota.assessor,
        folha=nota.folha,
        endereco=nota.endereco,
        cidade=nota.cidade,
        uf=nota.uf,
        cep=nota.cep,
        nota_de=None,
        local=None,
        emissor=None,
        cnpj_emissor=None,
        comando=None,
        mercado=None,
        status=None,
        liquido_para=nota.liquido_para,
        debentures=nota.debentures,
        vendas_a_vista=nota.vendas_a_vista,
        compras_a_vista=nota.compras_a_vista,
        opcoes_compras=nota.opcoes_compras,
        opcoes_vendas=nota.opcoes_vendas,
        operacoes_a_termo=nota.operacoes_a_termo,
        valor_das_operacoes_com_titulos_publicos=nota.valor_das_operacoes_com_titulos_publicos,
        valor_das_operacoes=nota.valor_das_operacoes,
        valor_liquido_das_operacoes=nota.valor_liquido_das_operacoes,
        taxa_de_liquidacao=nota.taxa_de_liquidacao,
        taxa_de_registro=nota.taxa_de_registro,
        total_clearing_cblc=nota.total_clearing_cblc,
        taxa_de_termo_opcoes=nota.taxa_de_termo_opcoes,
        taxa_a_n_a=nota.taxa_a_n_a,
        emolumentos=nota.emolumentos,
        total_bolsa=nota.total_bolsa,
        corretagem=nota.corretagem,
        iss=nota.iss,
        irrf_sobre_operacoes=nota.irrf_sobre_operacoes_base_0_00,
        irrf_day_trade=nota.irrf_day_trade,
        outras=nota.outras,
        total_corretagem_despesas=nota.total_corretagem_despesas,
        taxa_operacional=nota.taxa_operacional,
        execucao=nota.execucao,
        taxa_de_custodia=nota.taxa_de_custodia,
        impostos=nota.impostos,
        pis_cofins=nota.pis_cofins,
        taxa_de_transferencia_de_ativos=nota.taxa_de_transferencia_de_ativos,
        execucao_casa=nota.execucao_casa,
    )

    return DocumentoTransformado(nota=nota_record, negociacoes=negociacoes)


def _transformar_titulo_publico(doc: TituloPublico) -> DocumentoTransformado:
    valor_total = doc.valor_total or ZERO
    tipo = _tipo_titulo_publico(doc.tipo)

    negociacao = NegociacaoRecord(
        nota_id=doc.numero_da_nota,
        corretora_id=doc.corretora_id,
        doc_type="TituloPublico",
        linha_na_nota=0,
        raw_ticker=doc.titulo,
        data=doc.data_de_operacao,
        sentido=_sentido(tipo),
        tipo=tipo,
        debito_credito=None,
        quantidade=doc.quantidade,
        preco_unitario=doc.valor_1_titulo,
        valor_bruto=valor_total,
        taxas_proporcionais=ZERO,  # fees embedded in price for gov bonds
        valor_liquido=valor_total,
        mercado=doc.mercado,
        tipo_de_mercado=None,
        prazo=None,
        observacao=None,
        indexador=None,
        taxa_cupom_percentual=None,
        percentual_do_indexador=None,
        emissao=None,
        vencimento=None,
        custodia=None,
        tipo_emitente=None,
        conta_bancaria=None,
        rendimentos=None,
        imposto_de_renda_federal=None,
        iof=None,
        especificacao_observacao=None,
        tx_bvmf=doc.tx_bvmf,
        tx_agente_custodia=doc.tx_agente_custodia,
    )

    nota_record = NotaRecord(
        nota_id=doc.numero_da_nota,
        corretora_id=doc.corretora_id,
        doc_type="TituloPublico",
        data_pregao=doc.data_de_operacao,
        data_de_liquidacao=None,
        cpf_cliente=doc.cpf_cliente,
        codigo_cliente=doc.codigo_cliente,
        nome_cliente=doc.nome_cliente,
        assessor=None,
        folha=None,
        endereco=None,
        cidade=None,
        uf=None,
        cep=None,
        nota_de=None,
        local=None,
        emissor=None,
        cnpj_emissor=None,
        comando=None,
        mercado=doc.mercado,
        status=doc.status,
        liquido_para=valor_total,
        debentures=None,
        vendas_a_vista=None,
        compras_a_vista=None,
        opcoes_compras=None,
        opcoes_vendas=None,
        operacoes_a_termo=None,
        valor_das_operacoes_com_titulos_publicos=None,
        valor_das_operacoes=None,
        valor_liquido_das_operacoes=None,
        taxa_de_liquidacao=None,
        taxa_de_registro=None,
        total_clearing_cblc=None,
        taxa_de_termo_opcoes=None,
        taxa_a_n_a=None,
        emolumentos=None,
        total_bolsa=None,
        corretagem=None,
        iss=None,
        irrf_sobre_operacoes=None,
        irrf_day_trade=None,
        outras=None,
        total_corretagem_despesas=None,
        taxa_operacional=None,
        execucao=None,
        taxa_de_custodia=None,
        impostos=None,
        pis_cofins=None,
        taxa_de_transferencia_de_ativos=None,
        execucao_casa=None,
    )

    return DocumentoTransformado(nota=nota_record, negociacoes=[negociacao])


def _transformar_titulo_privado(doc: TituloPrivado) -> DocumentoTransformado:
    valor_bruto = doc.valor_da_operacao or ZERO
    valor_liquido = doc.valor_liquido or ZERO
    taxas = round(abs(valor_bruto - valor_liquido), 2)
    tipo = _tipo_titulo_privado(doc.nota_de)

    negociacao = NegociacaoRecord(
        nota_id=doc.numero_da_nota,
        corretora_id=doc.corretora_id,
        doc_type="TituloPrivado",
        linha_na_nota=0,
        raw_ticker=doc.titulo,
        data=doc.data_de_operacao,
        sentido=_sentido(tipo),
        tipo=tipo,
        debito_credito=None,
        quantidade=doc.quantidade_valor_nominal,
        preco_unitario=doc.preco_unitario_da_operacao,
        valor_bruto=valor_bruto,
        taxas_proporcionais=taxas,
        valor_liquido=valor_liquido,
        mercado=None,
        tipo_de_mercado=None,
        prazo=doc.prazo,
        observacao=None,
        indexador=doc.indexador,
        taxa_cupom_percentual=doc.taxa_cupom_percentual,
        percentual_do_indexador=doc.percentual_do_indexador,
        emissao=doc.emissao,
        vencimento=doc.vencimento,
        custodia=doc.custodia,
        tipo_emitente=doc.tipo_emitente,
        conta_bancaria=doc.conta_bancaria,
        rendimentos=doc.rendimentos,
        imposto_de_renda_federal=doc.imposto_de_renda_federal,
        iof=doc.iof,
        especificacao_observacao=doc.especificacao_observacao,
        tx_bvmf=None,
        tx_agente_custodia=None,
    )

    nota_record = NotaRecord(
        nota_id=doc.numero_da_nota,
        corretora_id=doc.corretora_id,
        doc_type="TituloPrivado",
        data_pregao=doc.data_de_operacao,
        data_de_liquidacao=doc.data_de_liquidacao,
        cpf_cliente=doc.cpf_cliente,
        codigo_cliente=doc.codigo_cliente,
        nome_cliente=doc.nome_cliente,
        assessor=None,
        folha=None,
        endereco=None,
        cidade=None,
        uf=None,
        cep=None,
        nota_de=doc.nota_de,
        local=doc.local,
        emissor=doc.emissor,
        cnpj_emissor=doc.cnpj_emissor,
        comando=doc.comando,
        mercado=None,
        status=None,
        liquido_para=valor_liquido,
        debentures=None,
        vendas_a_vista=None,
        compras_a_vista=None,
        opcoes_compras=None,
        opcoes_vendas=None,
        operacoes_a_termo=None,
        valor_das_operacoes_com_titulos_publicos=None,
        valor_das_operacoes=None,
        valor_liquido_das_operacoes=None,
        taxa_de_liquidacao=None,
        taxa_de_registro=None,
        total_clearing_cblc=None,
        taxa_de_termo_opcoes=None,
        taxa_a_n_a=None,
        emolumentos=None,
        total_bolsa=None,
        corretagem=None,
        iss=None,
        irrf_sobre_operacoes=None,
        irrf_day_trade=None,
        outras=doc.outras,
        total_corretagem_despesas=None,
        taxa_operacional=None,
        execucao=None,
        taxa_de_custodia=None,
        impostos=None,
        pis_cofins=None,
        taxa_de_transferencia_de_ativos=None,
        execucao_casa=None,
    )

    return DocumentoTransformado(nota=nota_record, negociacoes=[negociacao])
