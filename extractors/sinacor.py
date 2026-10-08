"""The market-standard nota layout ("Sinacor"), shared by XP, Brasil Plural and Safra.

Brokers print the same blocks with small differences in wording, accents,
spacing and position, so nothing here searches for exact strings:

- labels are matched on their letters and digits only ("Obs. (*)" and
  "Obs.(*)", "Especificação" and "Especificacao" are the same label);
- an amount is read with the C/D printed after it, so debits come out
  negative ("1.234,56 D" -> -1234.56), as the rest of the pipeline expects;
- in the trades table, the last four items of a row are always quantity,
  price, value and D/C (numbers are right-aligned and drift past their
  header); only the text columns before them are assigned by header position.

A row inside the trades table that does not have that shape raises an error
instead of being skipped: a missing trade would silently change the cost.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

import fitz

from models import Movimentacao, NotaCorretagem
from parsers import br_date_parser, br_number_parser, cpf_parser, money_parser

_DINHEIRO = re.compile(r"^-?\d+(?:\.\d{3})*,\d{2,}$")
_NUMERO = re.compile(r"^\d+(?:\.\d{3})*(?:,\d+)?$")
_DATA = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_HORA = re.compile(r"^\d{2}:\d{2}(?::\d{2})?$")
# Words that may sit between a summary label and its amount.
_ENTRE_ROTULO_E_VALOR = {"r$", "base", "|"}

# Summary fields: NotaCorretagem attribute -> label variants.
NEGOCIOS = {
    "debentures": ["Debêntures"],
    "vendas_a_vista": ["Vendas à vista"],
    "compras_a_vista": ["Compras à vista"],
    "opcoes_compras": ["Opções - compras"],
    "opcoes_vendas": ["Opções - vendas"],
    "operacoes_a_termo": ["Operações à termo"],
    "valor_das_operacoes_com_titulos_publicos": ["Valor das oper. c/ títulos públ. (v. nom.)", "Valor das oper. c/ títulos públ. (v. nom)"],
    "valor_das_operacoes": ["Valor das operações"],
}
FINANCEIRO = {
    "valor_liquido_das_operacoes": ["Valor líquido das operações"],
    "taxa_de_liquidacao": ["Taxa de liquidação"],
    "taxa_de_registro": ["Taxa de registro"],
    "total_clearing_cblc": ["Total CBLC"],
    "taxa_de_termo_opcoes": ["Taxa de termo/opções"],
    "taxa_a_n_a": ["Taxa A.N.A."],
    "emolumentos": ["Emolumentos"],
    "total_bolsa": ["Total Bovespa / Soma", "Total Bolsa / Soma"],
    "taxa_operacional": ["Taxa Operacional"],
    "execucao": ["Execução"],
    "execucao_casa": ["Execução casa"],
    "taxa_de_custodia": ["Taxa de Custódia"],
    "impostos": ["Impostos"],
    "taxa_de_transferencia_de_ativos": ["Taxa de transferência de Ativos", "Taxa de Transf. de Ativos"],
    "corretagem": ["Corretagem", "Clearing"],
    "iss": ["ISS ( São Paulo )"],
    "pis_cofins": ["Pis Cofins"],
    "irrf_sobre_operacoes_base_0_00": ["I.R.R.F. s/ operações"],
    "outras": ["Outras", "Outros"],
    "total_corretagem_despesas": ["Total corretagem / Despesas", "Total Custos / Despesas"],
    "liquido_para": ["Líquido para"],
}
OBRIGATORIOS = ("valor_liquido_das_operacoes", "liquido_para")
# Charges: always debits, whatever letter is printed next to them (some XP
# notas print C beside fees). Stored negative, like any debit.
TAXAS = (
    "taxa_de_liquidacao", "taxa_de_registro", "taxa_de_termo_opcoes", "taxa_a_n_a", "emolumentos",
    "total_bolsa", "taxa_operacional", "execucao", "execucao_casa", "taxa_de_custodia", "impostos",
    "taxa_de_transferencia_de_ativos", "corretagem", "iss", "pis_cofins",
    "irrf_sobre_operacoes_base_0_00", "outras", "total_corretagem_despesas",
)


def normalizar(texto: str) -> str:
    """Lowercase letters and digits only, accents removed."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return "".join(c for c in sem_acento.lower() if c.isalnum())


CENTAVO = Decimal("0.01")


@dataclass(frozen=True)
class Palavra:
    x0: float
    y0: float
    x1: float
    y1: float
    texto: str

    @property
    def norm(self) -> str:
        return normalizar(self.texto)


def linhas_da_pagina(page: fitz.Page) -> list[list[Palavra]]:
    """Words grouped into visual lines (by vertical center), left to right."""
    palavras = [Palavra(*w[:5]) for w in page.get_text("words")]
    linhas: list[tuple[float, float, list[Palavra]]] = []
    for p in sorted(palavras, key=lambda p: ((p.y0 + p.y1) / 2, p.x0)):
        centro = (p.y0 + p.y1) / 2
        tol = max(1.5, (p.y1 - p.y0) * 0.4)
        if linhas and abs(linhas[-1][0] - centro) <= tol:
            linhas[-1][2].append(p)
        else:
            linhas.append((centro, p.y1 - p.y0, [p]))
    return [sorted(ws, key=lambda p: p.x0) for _, _, ws in linhas]


def _ocorrencias(linhas: list[list[Palavra]], rotulo: str):
    """(line index, index of the word after the label, label words) wherever
    the label starts and ends on word boundaries."""
    alvo = normalizar(rotulo)
    for li, linha in enumerate(linhas):
        for inicio in range(len(linha)):
            acumulado = ""
            for fim in range(inicio, len(linha)):
                acumulado += linha[fim].norm
                if acumulado == alvo:
                    yield li, fim + 1, linha[inicio:fim + 1]
                    break
                if not alvo.startswith(acumulado):
                    break


def _valor_apos(linha: list[Palavra], inicio: int) -> str | None:
    """The amount printed after a label: skips 'R$', 'base' and dates, takes
    the last amount of the run, with the C/D that follows it."""
    valor = None
    sinal = ""
    for p in linha[inicio:]:
        texto = p.texto.strip().rstrip("|")
        if not texto or texto.lower() in _ENTRE_ROTULO_E_VALOR or _DATA.match(texto) or _HORA.match(texto):
            continue
        if _DINHEIRO.match(texto):
            valor, sinal = texto, ""
        elif texto in ("C", "D") and valor is not None:
            sinal = texto
        else:
            break
    if valor is None:
        return None
    return f"{valor} {sinal}".strip()


def valores_do_resumo(linhas: list[list[Palavra]], campos: dict[str, list[str]]) -> dict[str, Decimal | None]:
    """Each field's amount; the last occurrence wins (a nota over several
    pages repeats the summary, and only the last page has the totals)."""
    resultado = {}
    for campo, variantes in campos.items():
        achado = None
        for rotulo in variantes:
            for li, depois, _ in _ocorrencias(linhas, rotulo):
                valor = _valor_apos(linhas[li], depois)
                if valor is not None:
                    achado = valor
        resultado[campo] = money_parser(achado) if achado else None
    return resultado


def _rotulo(linhas, variantes: list[str]) -> list[Palavra] | None:
    """Words of the first occurrence of any of the label variants."""
    for rotulo in variantes:
        for _, _, palavras in _ocorrencias(linhas, rotulo):
            return palavras
    return None


def _abaixo(linhas, palavras: list[Palavra] | None, ate_x: float | None = None) -> str:
    """Value printed under a header label (nota number, date, CPF...): the
    first line below the label with words in the label's column. Labels are
    often in a smaller font, so 'below' compares vertical centers."""
    if not palavras:
        return ""
    x0 = palavras[0].x0
    centro = sum((p.y0 + p.y1) / 2 for p in palavras) / len(palavras)
    limite = ate_x if ate_x is not None else x0 + 150
    for linha in linhas:
        abaixo = [p for p in linha if (p.y0 + p.y1) / 2 > centro + 1.5 and x0 - 12 <= p.x0 < limite]
        if abaixo:
            return " ".join(p.texto for p in abaixo)
    return ""


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _cabecalho(linhas) -> dict[str, str]:
    numero = _rotulo(linhas, ["Nr. nota", "Nr Nota"])
    folha = _rotulo(linhas, ["Folha"])
    data = _rotulo(linhas, ["Data pregão"])
    if not (numero and data):
        raise ValueError("Cabeçalho da nota (número e data do pregão) não encontrado.")
    cpf = _rotulo(linhas, ["C.P.F./C.N.P.J/C.V.M./C.O.B."])
    codigo = _rotulo(linhas, ["Código cliente"])
    assessor = _rotulo(linhas, ["Assessor"])
    data_texto = _abaixo(linhas, data).split()
    return {
        "numero": _abaixo(linhas, numero, folha[0].x0 if folha else data[0].x0),
        "folha": _abaixo(linhas, folha, data[0].x0) if folha else "",
        "data": data_texto[0] if data_texto else "",
        "cpf": _abaixo(linhas, cpf),
        "codigo": _abaixo(linhas, codigo, assessor[0].x0 if assessor else None),
        "assessor": _abaixo(linhas, assessor),
    }


def _cpf(texto: str) -> str | None:
    """Sinacor prints the CPF as a number: leading zeros may be missing.
    Some XP notas leave the field blank: None, not an error."""
    digitos = "".join(c for c in texto if c.isdigit())
    if not digitos:
        return None
    if 9 <= len(digitos) < 11:
        digitos = digitos.zfill(11)
    return cpf_parser(digitos)


# ---------------------------------------------------------------------------
# Trades table
# ---------------------------------------------------------------------------

_COLUNAS = {
    "cv": ["C/V"],
    "tipo": ["Tipo mercado"],
    "prazo": ["Prazo"],
    "especificacao": ["Especificação do título"],
    "obs": ["Obs. (*)"],
    "quantidade": ["Quantidade"],
}


def _centro(linha: list[Palavra]) -> float:
    return sum((p.y0 + p.y1) / 2 for p in linha) / len(linha)


def _eh_cabecalho_da_tabela(linha: list[Palavra]) -> bool:
    texto = "".join(p.norm for p in linha)
    return "negociacao" in texto and "quantidade" in texto


def _eh_fim_da_tabela(linha: list[Palavra]) -> bool:
    texto = "".join(p.norm for p in linha)
    return texto.startswith(("resumodosnegocios", "resumodenegocios"))


def _x_do_rotulo(palavras: list[Palavra], rotulo: str) -> float | None:
    """x0 of a column label among the header words. The header may span
    several lines ("Especificação" above "do título") and labels may be fused
    into one word ("C/VTipo"): then the position inside the word is estimated."""
    achado = next(_ocorrencias([palavras], rotulo), None)
    if achado:
        return achado[2][0].x0
    primeira = normalizar(rotulo.split()[0])
    for p in palavras:
        pos = p.norm.find(primeira)
        if pos > 0:
            # Map the position in the normalized text back to the printed word.
            letras = [i for i, c in enumerate(p.texto) if normalizar(c)]
            return p.x0 + (p.x1 - p.x0) * letras[pos] / len(p.texto)
        if pos == 0:
            return p.x0
    return None


def _colunas(banda: list[Palavra]) -> dict[str, float]:
    """x0 of each column, from the words of the header band."""
    palavras = sorted(banda, key=lambda p: p.x0)
    xs = {}
    for coluna, variantes in _COLUNAS.items():
        for rotulo in variantes:
            x = _x_do_rotulo(palavras, rotulo)
            if x is not None:
                xs[coluna] = x
                break
        else:
            raise ValueError(f"Coluna '{variantes[0]}' não encontrada no cabeçalho da tabela de negócios.")
    return xs


def _limpo(p: Palavra) -> str:
    return p.texto.strip().rstrip("|")


def _eh_ancora(linha: list[Palavra]) -> bool:
    """The line of a trade that carries quantity, price, value and D/C."""
    t = [_limpo(p) for p in linha]
    return (len(t) >= 5 and t[-1] in ("C", "D") and bool(_DINHEIRO.match(t[-2]))
            and bool(_DINHEIRO.match(t[-3])) and bool(_NUMERO.match(t[-4])))


def _negocio(ancora: list[Palavra], extras: list[Palavra], xs: dict[str, float]) -> Movimentacao:
    textos = [_limpo(p) for p in ancora]
    valor, preco, quantidade = textos[-2], textos[-3], textos[-4]
    # Text cells may wrap above and below the anchor line: reading order.
    palavras = sorted(ancora[:-4] + extras, key=lambda p: (round((p.y0 + p.y1) / 2), p.x0))

    # Where each text column starts. Values often begin a little left of
    # their header (the security name more than anything), so the edge is
    # pulled left by part of the gap to the previous header; observation
    # flags sit under their header, so that edge stays tight.
    ordem = ["cv", "tipo", "prazo", "especificacao"]
    inicio = {"mercado": -1e9, "obs": xs["obs"] - 3}
    anterior = None
    for coluna in ordem:
        recuo = 15.0 if anterior is None else min(15.0, 0.6 * (xs[coluna] - xs[anterior]))
        inicio[coluna] = xs[coluna] - recuo
        anterior = coluna
    sequencia = ["mercado", *ordem, "obs"]

    def coluna(nome: str) -> str:
        i = sequencia.index(nome)
        de = inicio[nome]
        ate = inicio[sequencia[i + 1]] if i + 1 < len(sequencia) else 1e9
        return " ".join(p.texto for p in palavras if de <= p.x0 < ate).strip()

    return Movimentacao(
        mercado=coluna("mercado"),
        compra_venda=coluna("cv"),
        tipo_de_mercado=coluna("tipo"),
        prazo=coluna("prazo") or None,
        especificacao_do_titulo=coluna("especificacao"),
        observacao=coluna("obs") or None,
        quantidade=br_number_parser(quantidade),
        preco_ajuste=money_parser(preco),
        valor_ajuste=money_parser(valor),
        debito_credito=textos[-1],
    )


# A wrapped cell sits at most about a line away from its trade's anchor line.
_DISTANCIA_DE_FRAGMENTO = 9.0


def _negocios_do_corpo(corpo: list[list[Palavra]], xs: dict[str, float]) -> list[Movimentacao]:
    ancoras = [l for l in corpo if _eh_ancora(l)]
    extras: dict[int, list[Palavra]] = {id(a): [] for a in ancoras}
    for linha in corpo:
        if any(linha is a for a in ancoras):
            continue
        perto = min(ancoras, key=lambda a: abs(_centro(a) - _centro(linha)), default=None)
        if perto is not None and abs(_centro(perto) - _centro(linha)) <= _DISTANCIA_DE_FRAGMENTO:
            extras[id(perto)].extend(linha)
        elif any(_DINHEIRO.match(_limpo(p)) for p in linha):
            raise ValueError("Linha da tabela de negócios fora do formato (quantidade, preço, valor e D/C).")
        # else: page footer or blank space of a multi-page nota
    return [_negocio(a, extras[id(a)], xs) for a in ancoras]


def negocios(linhas: list[list[Palavra]]) -> list[Movimentacao]:
    """Every row of every trades table on the (merged) page."""
    resultado = []
    i = 0
    while i < len(linhas):
        if not _eh_cabecalho_da_tabela(linhas[i]):
            i += 1
            continue
        centro = _centro(linhas[i])
        banda = [p for l in linhas if abs(_centro(l) - centro) <= 8 for p in l]
        i += 1
        while i < len(linhas) and abs(_centro(linhas[i]) - centro) <= 8:
            i += 1
        corpo = []
        while i < len(linhas) and not _eh_fim_da_tabela(linhas[i]) and not _eh_cabecalho_da_tabela(linhas[i]):
            corpo.append(linhas[i])
            i += 1
        resultado.extend(_negocios_do_corpo(corpo, _colunas(banda)))
    return resultado


# ---------------------------------------------------------------------------
# The nota
# ---------------------------------------------------------------------------

def _sinal_do_liquido(resumo: dict) -> Decimal:
    """Net value of the trades, with the sign the trade totals give it.

    Some XP notas print C beside it even for a purchase; when the printed
    amount is sales minus purchases in absolute value, that difference's
    sign is the right one."""
    valor = resumo["valor_liquido_das_operacoes"]
    vendas = (resumo["vendas_a_vista"] or 0) + (resumo["opcoes_vendas"] or 0)
    compras = (resumo["compras_a_vista"] or 0) + (resumo["opcoes_compras"] or 0)
    esperado = round(vendas - compras, 2)
    if esperado and abs(abs(valor) - abs(esperado)) <= CENTAVO:
        return esperado
    return valor


def _sinal_do_liquido_para(resumo: dict) -> Decimal:
    """'Líquido para' is the net value minus the fees, and fees are never
    negative. Some XP notas print C beside it on a purchase: when the printed
    letter would make the fees negative, the other sign is the right one."""
    liquido = resumo["liquido_para"]
    if resumo["valor_liquido_das_operacoes"] - liquido < -CENTAVO:
        return -liquido
    return liquido


def _como_float(valor: Decimal | None) -> float | None:
    return None if valor is None else float(valor)


def _identidade(numero: str, pregao, movimentacoes: list[Movimentacao], liquido_para: Decimal) -> str:
    """The nota's id: printed number, trading date and a fingerprint of its trades.

    The printed number alone is not unique: newer XP notas print a short
    per-day sequence ("1"), and XP issues the stock and the options trades of
    a day as separate notas under one number. The fingerprint uses only
    quantities and amounts, so the same nota sent twice keeps the same id.
    Numbers enter it as they did when parsing produced floats ("100.0"),
    so ids of notas already loaded do not change."""
    negocios = "|".join(sorted(
        f"{m.compra_venda}:{_como_float(m.quantidade)}:{_como_float(m.valor_ajuste)}" for m in movimentacoes
    ))
    digest = hashlib.sha256(f"{negocios}|{_como_float(liquido_para)}".encode()).hexdigest()[:8]
    return f"{numero}-{pregao:%Y%m%d}-{digest}"


def extrair(page: fitz.Page, corretora_id: str) -> NotaCorretagem:
    linhas = linhas_da_pagina(page)
    cab = _cabecalho(linhas)
    resumo = valores_do_resumo(linhas, NEGOCIOS) | valores_do_resumo(linhas, FINANCEIRO)
    faltando = [c for c in OBRIGATORIOS if resumo[c] is None]
    if faltando:
        raise ValueError(f"Campos do resumo financeiro não encontrados: {', '.join(faltando)}")
    for campo in TAXAS:
        if resumo[campo] is not None:
            resumo[campo] = -abs(resumo[campo])
    resumo["valor_liquido_das_operacoes"] = _sinal_do_liquido(resumo)
    resumo["liquido_para"] = _sinal_do_liquido_para(resumo)
    pregao = br_date_parser(cab["data"])
    movimentacoes = negocios(linhas)
    return NotaCorretagem(
        corretora_id=corretora_id,
        numero_da_nota=_identidade(cab["numero"], pregao, movimentacoes, resumo["liquido_para"]),
        folha=cab["folha"] or None,
        data_pregao=pregao,
        cpf_cliente=_cpf(cab["cpf"]),
        codigo_cliente=cab["codigo"],
        assessor=cab["assessor"] or None,
        movimentacoes=movimentacoes,
        **resumo,
    )
