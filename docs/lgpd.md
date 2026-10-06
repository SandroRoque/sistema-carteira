# Dados pessoais e LGPD

Como o sistema trata dados pessoais e quais compromissos cada parte do código
precisa respeitar. Este documento é uma decisão de engenharia, não parecer
jurídico; a política de privacidade pública deriva dele.

## Princípio: o sistema não se importa com identidade

A conta é dona de tudo o que envia. O sistema **não verifica** de quem são os
documentos, não media acesso entre contas e não tenta saber quem é o titular de
um CPF. Quem envia declara, nos termos de uso, ter direito de usar os documentos.
Se duas pessoas quiserem, cada uma mantém sua própria conta com cópias
independentes dos mesmos documentos.

O CPF impresso nas notas serve apenas para **separar carteiras dentro de uma conta**:
posição e IR são calculados por pessoa, então notas de CPFs diferentes não podem se
misturar.

## Inventário

| Dado | Origem | Guardado? | Forma |
|---|---|---|---|
| E-mail, senha | cadastro | sim | e-mail em claro; senha só como hash (Argon2) |
| CPF | notas | **não** | HMAC-SHA256 com segredo do servidor (`investidores.cpf_hash`) + máscara `***.456.789-**` |
| Nome, endereço, CEP, código de cliente, assessor | notas | **não** | lidos na extração e descartados |
| Apelido da carteira ("Eu", "Esposa") | usuário | sim | texto livre |
| Operações, proventos, posições | notas e relatórios B3 | sim | é o serviço |
| PDF / xlsx originais | upload | **não** (só durante o processamento) | ver Retenção |

A minimização (art. 6º, III) é garantida pelo schema: `tabelas.notas` não tem colunas
para dados de identidade, então o loader não tem onde gravá-los. O teste
`tests/test_loader.py::test_dados_de_identidade_nao_sao_persistidos` falha se algum
dado de identidade voltar a ser persistido.

O HMAC é pseudonimização, não anonimização: com o segredo, um CPF candidato pode ser
testado. Por isso `CPF_HMAC_KEY` é tratado como segredo de produção (nunca em log, nunca
no repositório), e o dado continua sendo pessoal para fins da LGPD.

## Base legal e papéis

- Base legal: **execução de contrato** (art. 7º, V) — o tratamento é o próprio serviço
  pedido pelo titular da conta.
- Documentos de terceiros (ex.: notas do cônjuge) são enviados pelo titular da conta
  para uso pessoal. Tratamos tudo com as mesmas salvaguardas, sem distinção.
- O sistema não usa os dados para nenhuma outra finalidade (sem marketing, sem
  compartilhamento, sem treino de modelos).

## Direitos do titular (art. 18)

| Direito | Como é atendido |
|---|---|
| Confirmação e acesso | o próprio app mostra tudo o que está guardado |
| Portabilidade | exportação completa da conta em JSON (`/conta/exportar`) |
| Correção | edição de negociações, apelidos e cadastro |
| Eliminação | excluir conta (`/conta`, confirmada com a senha) apaga tudo em cascata (`ON DELETE CASCADE` desde `usuarios`), na hora |
| Informação | política de privacidade pública, com operadores e prazos abaixo |

Após a exclusão, os dados só persistem nos backups do provedor até o fim da janela de
retenção deles; a política de privacidade informa esse prazo.

## Retenção

- Arquivos enviados: guardados no banco (`uploads.conteudo`) apenas até o
  processamento terminar, com sucesso ou erro — em geral segundos. Uma restrição do
  banco (`ck_uploads_conteudo_so_ate_processar`) impede que o conteúdo continue
  gravado depois disso. Ficam o nome do arquivo, o resultado e o hash SHA-256 do
  conteúdo, para detectar reenvio do mesmo arquivo; somem com a conta.
- A leitura dos arquivos roda num processo separado (`isolamento.py`); mensagens de
  erro mostradas ou registradas nunca incluem o conteúdo do documento.
- Dados da carteira: enquanto a conta existir.
- Logs da aplicação: sem dados pessoais (ver Segurança); retenção do provedor.

## Segurança (art. 46)

- TLS em todo o tráfego; criptografia em repouso do provedor de banco.
- Isolamento entre contas: todo acesso filtra por `investidor_id`, e o banco reforça
  isso com row-level security (papel `carteira_app`), para que um filtro esquecido no
  código não vaze dados. Testado em `tests/test_rls.py`.
- Senhas com Argon2id e bloqueio após tentativas erradas; sessões com cookie
  `HttpOnly`/`Secure`/`SameSite=Lax`, guardadas só como hash; CSRF por token e mesma origem.
- CSP restrita a `'self'`: nenhum script de terceiros roda nas páginas.
- Nada de dados pessoais em logs ou mensagens de erro (`cpf_parser` não ecoa o valor;
  Sentry com `send_default_pii=False`).
- Conta demo pública usa apenas dados sintéticos e CPFs fictícios.
- Segredos (`DATABASE_URL`, `CPF_HMAC_KEY`, chave de sessão) só em variáveis de ambiente.

## Operadores e transferência internacional

A política de privacidade lista cada provedor (hospedagem, banco, e-mail,
monitoramento de erros), a finalidade e o país. Preferimos regiões no Brasil quando o
provedor oferece; quando não, a transferência internacional segue o art. 33.

## Incidentes

Incidente com risco relevante aos titulares é comunicado à ANPD e aos afetados
(art. 48), no prazo da regulamentação da ANPD. Registro de cada incidente, mesmo os não
comunicados, fica documentado.

## Encarregado

Canal de contato de privacidade publicado na política de privacidade. Como agente de
pequeno porte, a indicação formal de encarregado pode ser dispensada, mas o canal de
comunicação com titulares é obrigatório.
