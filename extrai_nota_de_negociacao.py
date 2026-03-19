import fitz
from layout_config import CORRETORAS, FIELD_CONFIG, LAYOUT_CONFIG


class PdfImagemError(Exception):
    pass


class NotaNegociacaoExtractor:
    def __init__(self, layout_config=None, field_config=None, corretoras=None, parser_registry=None):
        self.layout_config = layout_config or LAYOUT_CONFIG
        self.field_config = field_config or FIELD_CONFIG
        self.corretoras = corretoras or CORRETORAS
        self.parser_registry = parser_registry or {}

    def prepara_pagina_unica(self, pdf_path):
        """Lê um PDF e retorna um objeto Document em memória contendo uma única página fundida."""
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

    def extrair(self, pdf_path):
        """Orquestra a extração dos dados da nota de negociação."""
        doc_mesclado = self.prepara_pagina_unica(pdf_path)
        pagina = doc_mesclado[0]

        if self.eh_pdf_de_imagem(pagina):
            doc_mesclado.close()
            raise PdfImagemError("PDF sem texto extraível; provavelmente é um arquivo de imagem.")

        corretora = self.identificar_corretora(pagina)
        layout = self.identificar_layout(pagina, corretora)
        nota_crua = self.extrair_com_layout(pagina, layout)

        doc_mesclado.close()
        return nota_crua

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

    def identificar_layout(self, pagina, corretora):
        pass

    def score_layout(self, pagina, layout):
        pass

    def extrair_com_layout(self, pagina, layout):
        pass

    def extrair_grupo(self, pagina, group):
        pass

    def extrair_key_value_group(self, pagina, group):
        pass

    def extrair_table_group(self, pagina, group):
        pass

    def parse_field_value(self, field_id, raw_value, binding=None):
        pass

    def get_field_config(self, field_id):
        pass

    def get_corretora_config(self, corretora_id):
        for corretora in self.corretoras:
            if corretora.id == corretora_id:
                return corretora
        raise ValueError(f"Corretora '{corretora_id}' não encontrada.")
