from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Corretora:
    id: str
    nome: str
    cnpj: str
    aliases: list[str] = field(default_factory=list)
    header_lines: list[str] = field(default_factory=list)
    site: str | None = None


CORRETORAS: list["Corretora"] = [
    Corretora(
        id="brasil_plural",
        nome="Brasil Plural CCTVM S/A",
        cnpj="05.816.451/0001-15",
        aliases=[
            "BRASIL PLURAL",
            "BRASIL PLURAL CCTVM S/A",
            "120-BRASIL PLURAL CCTVM S/A-",
        ],
        header_lines=[
            "120-BRASIL PLURAL CCTVM S/A-",
            "C.N.P.J: 05.816.451/0001-15",
        ],
        site="www.brasilplural.com",
    ),
    Corretora(
        id="nu_invest",
        nome="Nu Investimentos S.A. - Corretora de Títulos e Valores Mobiliários",
        cnpj="62.169.875/0001-79",
        aliases=[
            "Nu Investimentos S.A.",
            "Nu Investimentos",
            "Nubank",
            "Nu",
        ],
        header_lines=[
            "Nu Investimentos S.A. - Corretora de Títulos e Valores Mobiliários",
            "CNPJ: 62.169.875/0001-79 | www.nuinvest.com.br",
            "Rua Capote Valente, nº 39, 2º andar",
            "conjunto 01, 6º andar, conjunto 09 e 8º andar, conjunto 03 - Pinheiros",
            "05409-000. | São Paulo. | SP | BR",
        ],
        site="www.nuinvest.com.br",
    ),
    Corretora(
        id="xp",
        nome="XP Investimentos Corretora de Câmbio, Títulos e Valores Mobiliários S.A.",
        cnpj="02.332.886/0001-04",
        aliases=[
            "XP INVESTIMENTOS",
            "XP INVESTIMENTOS CCTVM S/A",
            "XP INVESTIMENTOS CORRETORA DE CÂMBIO, TÍTULOS E VALORES MOBILIÁRIOS S.A.",
        ],
        header_lines=[
            "XP INVESTIMENTOS CCTVM S/A",
            "XP INVESTIMENTOS CORRETORA DE CÂMBIO, TÍTULOS E VALORES MOBILIÁRIOS S.A.",
            "C.N.P.J: 02.332.886/0001-04",
        ],
        site="www.xpi.com.br",
    ),
    Corretora(
        id="safra",
        nome="Safra Distribuidora de Títulos e Valores Mobiliários Ltda",
        cnpj="01.638.542/0001-57",
        aliases=[
            "SAFRA",
            "SAFRA DISTRIBUIDORA DE TITULOS E VALORES MOBILIARIOS LTDA",
            "SAFRA DISTRIBUIDORA DE TÍTULOS E VALORES MOBILIÁRIOS LTDA",
        ],
        header_lines=[
            "SAFRA DISTRIBUIDORA DE TITULOS E VALORES MOBILIARIOS LTDA",
            "Internet: www.safracorretora.com.br",
            "C.N.P.J: 01.638.542/0001-57",
        ],
        site="www.safracorretora.com.br",
    ),
]


@dataclass
class Movimentacao:
    mercado: str
    compra_venda: str
    tipo_de_mercado: str
    especificacao_do_titulo: str
    quantidade: Decimal | None
    preco_ajuste: Decimal | None
    valor_ajuste: Decimal | None
    debito_credito: str
    prazo: str | None = None
    observacao: str | None = None

    def validate(self) -> None:
        missing = [
            # tipo_de_mercado may be blank: newer XP notas leave it empty for regular trades.
            f for f in ["mercado", "compra_venda", "especificacao_do_titulo", "debito_credito"]
            if not getattr(self, f)
        ]
        if self.quantidade is None:
            missing.append("quantidade")
        if self.preco_ajuste is None:
            missing.append("preco_ajuste")
        if self.valor_ajuste is None:
            missing.append("valor_ajuste")
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes na movimentação: {', '.join(missing)}")


@dataclass
class NotaCorretagem:
    corretora_id: str
    numero_da_nota: str
    data_pregao: date
    # None when the nota leaves it blank (some XP notas): the loader then
    # needs the portfolio from elsewhere (loader.investidor_da_nota).
    cpf_cliente: str | None
    codigo_cliente: str
    # optional header fields
    folha: str | None = None
    nome_cliente: str | None = None
    assessor: str | None = None
    endereco: str | None = None
    cidade: str | None = None
    uf: str | None = None
    cep: str | None = None
    # resumo dos negócios
    debentures: Decimal | None = None
    vendas_a_vista: Decimal | None = None
    compras_a_vista: Decimal | None = None
    opcoes_compras: Decimal | None = None
    opcoes_vendas: Decimal | None = None
    operacoes_a_termo: Decimal | None = None
    valor_das_operacoes_com_titulos_publicos: Decimal | None = None
    valor_das_operacoes: Decimal | None = None
    # resumo financeiro (all optional — broker-specific subsets)
    valor_liquido_das_operacoes: Decimal | None = None
    taxa_de_liquidacao: Decimal | None = None
    taxa_de_registro: Decimal | None = None
    total_clearing_cblc: Decimal | None = None
    taxa_de_termo_opcoes: Decimal | None = None
    taxa_a_n_a: Decimal | None = None
    emolumentos: Decimal | None = None
    total_bolsa: Decimal | None = None
    corretagem: Decimal | None = None
    iss: Decimal | None = None
    irrf_sobre_operacoes_base_0_00: Decimal | None = None
    outras: Decimal | None = None
    total_corretagem_despesas: Decimal | None = None
    liquido_para: Decimal | None = None
    # XP-specific
    taxa_operacional: Decimal | None = None
    execucao: Decimal | None = None
    taxa_de_custodia: Decimal | None = None
    impostos: Decimal | None = None
    # Safra-specific
    pis_cofins: Decimal | None = None
    taxa_de_transferencia_de_ativos: Decimal | None = None
    execucao_casa: Decimal | None = None
    # movimentações table
    movimentacoes: list[Movimentacao] = field(default_factory=list)

    def validate(self) -> None:
        missing = []
        if not self.numero_da_nota:
            missing.append("numero_da_nota")
        if not self.data_pregao:
            missing.append("data_pregao")
        if not self.codigo_cliente:
            missing.append("codigo_cliente")
        if not self.movimentacoes:
            missing.append("movimentacoes (lista vazia)")
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes: {', '.join(missing)}")
        for i, mov in enumerate(self.movimentacoes):
            try:
                mov.validate()
            except ValueError as exc:
                raise ValueError(f"Movimentação {i + 1}: {exc}") from exc


@dataclass
class TituloPublico:
    corretora_id: str
    numero_da_nota: str
    nome_cliente: str
    cpf_cliente: str
    codigo_cliente: str
    mercado: str
    status: str
    tipo: str
    data_de_operacao: date
    titulo: str
    quantidade: Decimal | None
    valor_total: Decimal | None
    valor_1_titulo: Decimal | None = None
    tx_bvmf: Decimal | None = None
    tx_agente_custodia: Decimal | None = None

    def validate(self) -> None:
        missing = [
            f for f in ["numero_da_nota", "nome_cliente", "cpf_cliente", "codigo_cliente",
                        "mercado", "status", "tipo", "titulo"]
            if not getattr(self, f)
        ]
        if not self.data_de_operacao:
            missing.append("data_de_operacao")
        if self.quantidade is None:
            missing.append("quantidade")
        if self.valor_total is None:
            missing.append("valor_total")
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes: {', '.join(missing)}")


@dataclass
class TituloPrivado:
    corretora_id: str
    numero_da_nota: str
    nome_cliente: str
    cpf_cliente: str
    codigo_cliente: str
    nota_de: str
    emissor: str
    data_de_operacao: date
    data_de_liquidacao: date
    cnpj_emissor: str
    indexador: str
    titulo: str
    quantidade_valor_nominal: Decimal | None
    preco_unitario_da_operacao: Decimal | None
    valor_da_operacao: Decimal | None
    valor_liquido: Decimal | None
    # optional
    local: str | None = None
    conta_bancaria: str | None = None
    tipo_emitente: str | None = None
    taxa_cupom_percentual: Decimal | None = None
    percentual_do_indexador: Decimal | None = None
    prazo: str | None = None
    custodia: str | None = None
    emissao: date | None = None
    vencimento: date | None = None
    rendimentos: str | None = None
    imposto_de_renda_federal: Decimal | None = None
    iof: Decimal | None = None
    outras: Decimal | None = None
    especificacao_observacao: str | None = None
    comando: str | None = None

    def validate(self) -> None:
        missing = [
            f for f in ["numero_da_nota", "nome_cliente", "cpf_cliente", "codigo_cliente",
                        "emissor", "cnpj_emissor", "titulo", "indexador"]
            if not getattr(self, f)
        ]
        if not self.data_de_operacao:
            missing.append("data_de_operacao")
        if not self.data_de_liquidacao:
            missing.append("data_de_liquidacao")
        if self.quantidade_valor_nominal is None:
            missing.append("quantidade_valor_nominal")
        if self.valor_da_operacao is None:
            missing.append("valor_da_operacao")
        if self.valor_liquido is None:
            missing.append("valor_liquido")
        if missing:
            raise ValueError(f"Campos obrigatórios ausentes: {', '.join(missing)}")
