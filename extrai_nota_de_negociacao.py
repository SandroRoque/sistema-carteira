import re

import fitz

from extractors import EXTRACTORS
from models import CORRETORAS, NotaCorretagem, TituloPrivado, TituloPublico


class PdfImagemError(Exception):
    pass


class LayoutNaoSuportado(Exception):
    """A known broker, in a layout no extractor reads yet."""


# The nota number on a page, in the layouts seen so far: "Número da nota"
# above or beside it, or the Sinacor header "Nr. Nota / Folha / Data pregão".
_NUMERO_DA_NOTA = re.compile(
    r"(?:Número da nota|Nr\. ?[Nn]ota)\s*(?:Folha\s*Data preg[ãa]o\s*)?(\d+)"
)


def _normalize_text(text: str) -> str:
    return "".join(char.lower() for char in text if char.isalnum())


def _abrir(pdf) -> fitz.Document:
    if isinstance(pdf, (bytes, bytearray)):
        doc = fitz.open(stream=pdf, filetype="pdf")
    else:
        doc = fitz.open(pdf)
    if doc.page_count == 0:
        doc.close()
        raise ValueError("O PDF não contém páginas.")
    return doc


def _fundir(doc: fitz.Document, paginas: list[int]) -> fitz.Document:
    """A Document with the given pages stacked into one tall page."""
    doc_mesclado = fitz.open()
    if len(paginas) == 1:
        doc_mesclado.insert_pdf(doc, from_page=paginas[0], to_page=paginas[0])
        return doc_mesclado
    altura_total = sum(doc[i].rect.height for i in paginas)
    largura_maxima = max(doc[i].rect.width for i in paginas)
    pagina = doc_mesclado.new_page(width=largura_maxima, height=altura_total)
    y_offset = 0
    for i in paginas:
        rect_destino = fitz.Rect(0, y_offset, doc[i].rect.width, y_offset + doc[i].rect.height)
        pagina.show_pdf_page(rect_destino, doc, i)
        y_offset += doc[i].rect.height
    return doc_mesclado


def agrupar_paginas(doc: fitz.Document) -> list[list[int]]:
    """Pages grouped by nota: brokers bundle several notas (one per trading
    day) in one PDF. A page without a recognizable number continues the
    previous nota; with fewer than two numbers the whole PDF is one nota."""
    numeros = []
    for page in doc:
        m = _NUMERO_DA_NOTA.search(page.get_text())
        numeros.append(m.group(1) if m else None)
    if len({n for n in numeros if n}) < 2:
        return [list(range(doc.page_count))]
    grupos: list[list[int]] = []
    atual = None
    for i, numero in enumerate(numeros):
        if grupos and (numero is None or numero == atual):
            grupos[-1].append(i)
        else:
            grupos.append([i])
            atual = numero
    return grupos


def prepara_pagina_unica(pdf) -> fitz.Document:
    """Lê um PDF (caminho ou bytes) e retorna um Document em memória contendo
    uma única página fundida."""
    doc = _abrir(pdf)
    try:
        return _fundir(doc, list(range(doc.page_count)))
    finally:
        doc.close()


def eh_pdf_de_imagem(pagina: fitz.Page, min_text_chars: int = 20) -> bool:
    return len(pagina.get_text("text").strip()) < min_text_chars


def identificar_corretora(pagina: fitz.Page, corretoras=None):
    corretoras = corretoras or CORRETORAS
    normalized_page_text = _normalize_text(pagina.get_text("text"))

    best_match = None
    best_score = 0

    for corretora in corretoras:
        score = 0
        for header_line in corretora.header_lines:
            if _normalize_text(header_line) in normalized_page_text:
                score += 3
        for alias in corretora.aliases:
            if _normalize_text(alias) in normalized_page_text:
                score += 1
        if _normalize_text(corretora.cnpj) in normalized_page_text:
            score += 4

        if score > best_score:
            best_match = corretora
            best_score = score

    if not best_match:
        raise ValueError("Nenhuma corretora reconhecida no documento.")

    return best_match


def _extrair_pagina(pagina: fitz.Page) -> NotaCorretagem | TituloPublico | TituloPrivado:
    if eh_pdf_de_imagem(pagina):
        raise PdfImagemError("PDF sem texto extraível; provavelmente é um arquivo de imagem.")
    corretora = identificar_corretora(pagina)
    extractor = EXTRACTORS.get(corretora.id)
    if extractor is None:
        raise ValueError(f"Nenhum extrator implementado para a corretora '{corretora.id}'.")
    resultado = extractor.extract(pagina)
    resultado.validate()
    return resultado


def extrair_notas(pdf) -> list[NotaCorretagem | TituloPublico | TituloPrivado]:
    """Every nota in the PDF (caminho ou bytes), in page order."""
    doc = _abrir(pdf)
    try:
        resultados = []
        for paginas in agrupar_paginas(doc):
            fundido = _fundir(doc, paginas)
            try:
                resultados.append(_extrair_pagina(fundido[0]))
            finally:
                fundido.close()
        return resultados
    finally:
        doc.close()


def extrair(pdf) -> NotaCorretagem | TituloPublico | TituloPrivado:
    """The nota of a single-nota PDF (see extrair_notas for bundles)."""
    resultados = extrair_notas(pdf)
    if len(resultados) != 1:
        raise ValueError(f"O PDF contém {len(resultados)} notas; use extrair_notas.")
    return resultados[0]

