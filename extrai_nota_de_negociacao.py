import fitz

from layout_config import CORRETORAS


class NotaNegociacaoExtractor:
    """Low-level PDF helpers used by the orchestration in main.py."""

    def __init__(self, corretoras=None):
        self.corretoras = corretoras or CORRETORAS

    def prepara_pagina_unica(self, pdf_path):
        """Le um PDF e retorna um documento em memoria com uma pagina fundida."""
        doc = fitz.open(pdf_path)

        if doc.page_count == 0:
            doc.close()
            raise ValueError("O PDF nao contem paginas.")

        doc_mesclado = fitz.open()

        if doc.page_count == 1:
            doc_mesclado.insert_pdf(doc)
        else:
            altura_total = sum(page.rect.height for page in doc)
            largura_maxima = max(page.rect.width for page in doc)
            pagina = doc_mesclado.new_page(width=largura_maxima, height=altura_total)

            y_offset = 0
            for index, page in enumerate(doc):
                rect_destino = fitz.Rect(
                    0,
                    y_offset,
                    page.rect.width,
                    y_offset + page.rect.height,
                )
                pagina.show_pdf_page(rect_destino, doc, index)
                y_offset += page.rect.height

        doc.close()
        return doc_mesclado

    def normalize_text_for_matching(self, text):
        return "".join(char.lower() for char in text if char.isalnum())

    def eh_pdf_de_imagem(self, pagina, min_text_chars=20):
        text = pagina.get_text("text").strip()
        return len(text) < min_text_chars

    def identificar_corretora(self, pagina):
        page_text = pagina.get_text("text")
        normalized_page_text = self.normalize_text_for_matching(page_text)

        best_match = None
        best_score = 0

        for corretora in self.corretoras:
            score = 0
            for header_line in corretora.header_lines:
                if self.normalize_text_for_matching(header_line) in normalized_page_text:
                    score += 3
            for alias in corretora.aliases:
                if self.normalize_text_for_matching(alias) in normalized_page_text:
                    score += 1
            if self.normalize_text_for_matching(corretora.cnpj) in normalized_page_text:
                score += 4

            if score > best_score:
                best_match = corretora
                best_score = score

        if not best_match:
            raise ValueError("Nenhuma corretora reconhecida no documento.")

        return best_match
