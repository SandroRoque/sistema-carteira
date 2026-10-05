import fitz

from extractors import EXTRACTORS
from models import CORRETORAS, NotaCorretagem, TituloPrivado, TituloPublico


class PdfImagemError(Exception):
    pass


def _normalize_text(text: str) -> str:
    return "".join(char.lower() for char in text if char.isalnum())


def prepara_pagina_unica(pdf_path) -> fitz.Document:
    """Lê um PDF e retorna um Document em memória contendo uma única página fundida."""
    doc = fitz.open(pdf_path)

    if doc.page_count == 0:
        doc.close()
        raise ValueError("O PDF não contém páginas.")

    doc_mesclado = fitz.open()

    if doc.page_count == 1:
        doc_mesclado.insert_pdf(doc)
    else:
        altura_total = sum(page.rect.height for page in doc)
        largura_maxima = max(page.rect.width for page in doc)

        pagina = doc_mesclado.new_page(width=largura_maxima, height=altura_total)

        y_offset = 0
        for i, page in enumerate(doc):
            rect_destino = fitz.Rect(0, y_offset, page.rect.width, y_offset + page.rect.height)
            pagina.show_pdf_page(rect_destino, doc, i)
            y_offset += page.rect.height

    doc.close()
    return doc_mesclado


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


def extrair(pdf_path) -> NotaCorretagem | TituloPublico | TituloPrivado:
    """Orquestra a extração dos dados da nota de negociação."""
    doc = prepara_pagina_unica(pdf_path)
    pagina = doc[0]

    if eh_pdf_de_imagem(pagina):
        doc.close()
        raise PdfImagemError("PDF sem texto extraível; provavelmente é um arquivo de imagem.")

    corretora = identificar_corretora(pagina)
    extractor = EXTRACTORS.get(corretora.id)
    if extractor is None:
        doc.close()
        raise ValueError(f"Nenhum extrator implementado para a corretora '{corretora.id}'.")

    resultado = extractor.extract(pagina)
    doc.close()
    resultado.validate()
    return resultado

