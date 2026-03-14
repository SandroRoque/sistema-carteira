import os
import fitz
import pandas as pd


CONFIG_CAMPOS = {
    "numero_da_nota": {
        "label": "Número da nota",
        "value_relative_position": ["same column", "next row"],
        "expect_single": True
    },
    "data_pregao": {
        "label": "Data pregão",
        "value_relative_position": ["same column", "next row"],
        "expect_single": True
    },
    "nome_cliente": {
        "label": "Nome do Cliente",
        "value_relative_position": ["same column", "next row"],
        "expect_single": True
    },
    "cpf_cliente": {
        "label": "CPF",
        "value_relative_position": ["same column", "next row"],
        "expect_single": True
    },
    "codigo_do_cliente": {
        "label": "Código do Cliente",
        "value_relative_position": ["same column", "next row"],
        "expect_single": True
    },
    # now we need to extract the table of transactions, which has the following columns:
    # "Mercado", "C/V", "Tipode Mercado", "Especificação do Título", "Observação", "Quantidade", "Preço/Ajuste", "Valor/Ajuste", "D/C"
    # we will extract the table as a list of dictionaries, where each dictionary represents a row of the table, and the keys of the dictionary are the column names
    #
}

def prepara_pagina_unica(pdf_path):
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

def extrai_nota_de_negociacao(pdf_path):
    """Orquestra a extração dos dados da nota de negociação."""
    doc_mesclado = prepara_pagina_unica(pdf_path)
    pagina = doc_mesclado[0]
    
    # Aqui você aplicará a lógica do CONFIG_CAMPOS sobre a 'pagina'
    nota_crua = {}
    
    doc_mesclado.close()
    return nota_crua

if __name__ == "__main__":
    # Ensure our data directories exist
    os.makedirs("data/notas_de_negociacao", exist_ok=True)
    
    # Our test pdf is at cwd + "data/notas_de_negociacao/3019.pdf"
    pdf_path = "data/notas_de_negociacao/3019.pdf"
    # let's run the find_tables method from pymupdf to see what tables it finds in the pdf
    documento = fitz.open(pdf_path)
    todas_as_tabelas = []
    for pagina in documento:
        # A tabela de movimentações começa logo após os dados do cliente.
        # Já começa com o cabeçalho "Mercado", "C/V", "Tipode Mercado", "Especificação do Título", "Observação", "Quantidade", "Preço/Ajuste", "Valor/Ajuste", "D/C"
        # A tabela de movimentações termina antes do "Resumo dos Negócios", que é uma seção logo abaixo da tabela.

        # 1. Encontrar as âncoras (use textos que sempre aparecem antes e depois da tabela)
        # 1.A - Encontrar onde temos todos os campos do cabeçalho em uma mesma posição horizontal (mesma linha)

        ancora_inicio = pagina.search_for("Negócios realizados") # Exemplo: cabeçalho principal
        ancora_fim = pagina.search_for("Resumo dos Negócios")    # Exemplo: seção logo abaixo
        
        if ancora_inicio:
            # Pegar a coordenada Y do topo da âncora de início
            y_topo = ancora_inicio[0].y0
            
            # Pegar a coordenada Y do topo da âncora de fim (ou o fim da página se não encontrar)
            y_base = ancora_fim[0].y0 if ancora_fim else pagina.rect.height
            
            # 2. Criar a área de recorte (clip): (x0, y0, x1, y1)
            # Usamos 0 e a largura total (rect.width) para cobrir toda a extensão horizontal
            area_recorte = fitz.Rect(0, y_topo, pagina.rect.width, y_base)
            
            # 3. Restringir a busca de tabelas a essa área
            tabelas_encontradas = pagina.find_tables(clip=area_recorte)
            
            print(f"Encontradas {len(tabelas_encontradas.tables)} tabelas na página {pagina.number} usando clip")
            
            for tabela in tabelas_encontradas.tables:
                # Converter para pandas DataFrame usando to_pandas()
                df = tabela.to_pandas()
                print(df.head())
                todas_as_tabelas.append(df)

        # so it does find a table, let's see if we can extract the data from it
        for tabela in tabelas_encontradas.tables:
            # let's convert the table to a pandas dataframe
            df = pd.DataFrame(tabela.cells)
            print(df)