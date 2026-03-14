# Sistema de Carteira - Backend de ETL

## Status da Investigação de Extração de PDFs

Atualmente estamos avaliando a melhor estratégia para extrair os dados das Notas de Negociação no formato PDF usando a biblioteca `PyMuPDF`.

### 1. Formato de Configuração (`CONFIG_CAMPOS`)
Ainda estamos investigando se manteremos o formato atual de configuração para a extração dos campos. Especificamente, se a lógica de posições relativas (`value_relative_position` como `["same column", "next row"]`) é realmente necessária e robusta frente à variedade de layouts de corretoras.

### 2. Confiabilidade do método `find_tables()`
Houve falhas ao tentar usar o método nativo `find_tables()` para extrair a tabela de movimentações olhando a página inteira de uma vez. A tabela e as linhas não foram detectadas corretamente na varredura padrão.

Neste momento, a investigação foca num novo modelo usando **âncoras de texto** (buscando textos como "Negócios realizados" e "Resumo dos Negócios") para delimitar cirurgicamente uma área de busca (Bounding Box / `clip`) e passar apenas essa região para o `find_tables()`. Se este método garantir confiabilidade, continuaremos sem dependências pesadas adicionais.
