import os
import fitz
import pandas as pd
from layout_config import LAYOUT_CONFIG


class NotaNegociacaoExtractor:
    def __init__(self, layout_config=None):
        self.layout_config = layout_config or LAYOUT_CONFIG

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

        # Aqui você aplicará a lógica do layout sobre a 'pagina'
        nota_crua = {}

        doc_mesclado.close()
        return nota_crua

    def identificar_layout(self, pagina):
        pass

    def extrair_campos_vertical(self, pagina, campos):
        pass

    def extrair_campos_sbs(self, pagina, campos):
        pass

    def extrair_tabelas(self, pagina, tabelas):
        pass


if __name__ == "__main__":
    notas_dir = os.environ.get("NOTAS_DIR")
    if not notas_dir:
        raise ValueError("A variável de ambiente NOTAS_DIR não está definida.")

    pdf_files = sorted(
        file_name for file_name in os.listdir(notas_dir) if file_name.lower().endswith(".pdf")
    )

    for file_name in pdf_files:
        pdf_path = os.path.join(notas_dir, file_name)
        documento = fitz.open(pdf_path)
        todas_as_tabelas = []

        print(f"Processando {file_name}")

        for pagina in documento:
            ancora_inicio = pagina.search_for("Negócios realizados")
            ancora_fim = pagina.search_for("Resumo dos Negócios")

            if ancora_inicio:
                y_topo = ancora_inicio[0].y0
                y_base = ancora_fim[0].y0 if ancora_fim else pagina.rect.height

                area_recorte = fitz.Rect(0, y_topo, pagina.rect.width, y_base)
                tabelas_encontradas = pagina.find_tables(clip=area_recorte)

                print(f"Encontradas {len(tabelas_encontradas.tables)} tabelas na página {pagina.number} usando clip")

                for tabela in tabelas_encontradas.tables:
                    df = tabela.to_pandas()
                    print(df.head())
                    todas_as_tabelas.append(df)

            for tabela in tabelas_encontradas.tables:
                df = pd.DataFrame(tabela.cells)
                print(df)

        documento.close()
