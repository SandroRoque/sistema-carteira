# Deploy

A aplicação roda como **um** container no [Fly.io](https://fly.io) (região `gru`) com
PostgreSQL no [Neon](https://neon.tech) (região AWS São Paulo, `sa-east-1`). O worker de
importação é uma thread do próprio processo web; não há fila, storage ou serviço extra.

| Arquivo | Papel |
|---|---|
| `Dockerfile` | Imagem com as dependências de produção (sem testes nem dados locais, ver `.dockerignore`) |
| `fly.toml` | Máquina de 512 MB que para sem tráfego, checagem em `/saude`; o `release_command` aplica as migrações e recria a demonstração |
| `.github/workflows/ci.yml` | Testes e build da imagem em todo push para `main` e em PRs |
| `.github/workflows/deploy.yml` | Deploy manual (botão *Run workflow* no GitHub) |

## Variáveis

| Variável | Onde | Observação |
|---|---|---|
| `DATABASE_URL` | secret | Endpoint **direto** do Neon (ver abaixo) |
| `CPF_HMAC_KEY` | secret | Mínimo 32 caracteres. Guarde uma cópia fora do Fly: perder ou trocar a chave faz novos envios não reconhecerem as carteiras existentes |
| `CADASTRO_ABERTO` | `fly.toml` | `false` até o envio de e-mails estar configurado e testado (abaixo) |
| `URL_PUBLICA` | `fly.toml` | Endereço usado nos links enviados por e-mail; nunca é tirado do header `Host` |
| `SMTP_HOST`, `SMTP_PORTA`, `SMTP_USUARIO` | secret | Servidor de envio do provedor de e-mail transacional. Porta 465 usa TLS direto; as outras exigem STARTTLS (padrão 587) |
| `SMTP_SENHA` | secret | Senha ou chave de API do provedor |
| `EMAIL_REMETENTE` | secret | Ex.: `Carteira <nao-responda@seu-dominio>`; o domínio precisa de SPF e DKIM no provedor |
| `COOKIE_SECURE` | — | Padrão `true`, correto em produção (o Fly força HTTPS) |

## Banco no Neon

1. Crie um projeto na região **AWS São Paulo**.
2. Copie a connection string **sem pooling** (host sem `-pooler`). A conexão usa estado
   de sessão — `SET ROLE carteira_app` e `app.usuario_id` (ver `database.py`) — e um
   pooler em modo transação poderia entregar esse estado a outra requisição. O pool do
   SQLAlchemy dentro do processo já reaproveita conexões.
3. Troque o esquema para o driver usado pela aplicação:

   ```
   postgresql+psycopg://USUARIO:SENHA@ep-xxxx.sa-east-1.aws.neon.tech/neondb?sslmode=require
   ```

O dono do banco no Neon não é superusuário, mas tem `CREATEROLE`, que é o que a
migração 0003 precisa para criar o papel `carteira_app`. Esse caminho foi testado
com Postgres 17 e um dono sem superusuário.

## Primeiro deploy

```bash
fly auth login
fly apps create sistema-carteira        # se o nome estiver ocupado, troque também em fly.toml
fly secrets set \
  DATABASE_URL='postgresql+psycopg://...' \
  CPF_HMAC_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
fly deploy --ha=false                   # uma máquina só (o worker roda nela)
```

O `release_command` roda `alembic upgrade head` e `python admin.py recriar-demo` antes de
cada versão entrar no ar; se algum dos dois falhar, a versão anterior continua servindo.

## E-mail

Confirmação de cadastro e "esqueci a senha" enviam e-mail por SMTP (`correio.py`). Sem
`SMTP_HOST` ou sem `URL_PUBLICA`, essas páginas avisam que o envio está indisponível, a
senha só muda por `admin.py definir-senha` e o cadastro público fica fechado mesmo com
`CADASTRO_ABERTO=true` (uma conta nova não teria como confirmar o e-mail).

Qualquer provedor com SMTP serve. Configure o domínio do remetente (SPF e DKIM) no
provedor e então:

```bash
fly secrets set SMTP_HOST=smtp.provedor.com SMTP_USUARIO=... SMTP_SENHA=... \
  EMAIL_REMETENTE='Carteira <nao-responda@seu-dominio>'
```

Teste pedindo uma nova senha em `/senha/esqueci` para a sua própria conta. Uma falha de
envio aparece em `fly logs` só pelo tipo do erro (`falha ao enviar e-mail: ...`), porque a
mensagem carrega o link.

## Conta de demonstração

`admin.py recriar-demo` apaga e recria a conta de demonstração com uma carteira inventada
(`demo.py`): dois anos de notas e extrato da B3 com datas relativas a hoje, passando pelos
mesmos carregadores de um envio. O login mostra o botão "Ver demonstração", que entra sem
senha; a conta é somente leitura (toda escrita responde 403, exceto sair). Como cada deploy
a recria, as datas acompanham o calendário; sem deploys, rode o comando por `fly ssh console`
para atualizá-la. Para tirar a demonstração do ar, apague a conta marcada com `demo`.

Crie a primeira conta (o cadastro público está fechado):

```bash
fly ssh console
python admin.py criar-usuario voce@exemplo.com --admin
```

## Deploys seguintes

- Pelo terminal: `fly deploy`.
- Pelo GitHub: *Actions → Deploy → Run workflow*. Precisa do secret `FLY_API_TOKEN`
  (`fly tokens create deploy`) no environment `producao` do repositório; configure
  revisores obrigatórios nesse environment se quiser uma aprovação antes de cada deploy.

## Operação

- **Parada automática**: sem tráfego, o Fly para a máquina; a próxima requisição a acorda
  em poucos segundos. Uma importação interrompida volta para a fila e é retomada quando a
  máquina sobe (`importacao.py`). Enquanto há envio em andamento, a página de importação
  consulta o progresso a cada 2 s, o que mantém a máquina acordada.
- **Memória**: medido no container, processo web ~125 MB e processo de leitura de PDF até
  ~100 MB a mais. 512 MB deixa folga; acompanhe com `fly logs` (OOM aparece ali).
- **Logs**: `fly logs`. Erros de importação registram só o tipo da exceção, nunca o
  conteúdo do documento.
- **Backups**: o Neon guarda histórico para restauração por ponto no tempo; o tamanho da
  janela depende do plano. Confira antes de abrir para outras pessoas.
- **Mais de uma máquina**: funciona (as tarefas são reservadas com `SKIP LOCKED`), mas um
  envio só acorda o worker da máquina que o recebeu; ver TODO.md.

## Antes de abrir o cadastro

- Escolher o provedor de e-mail, configurar o envio (seção E-mail) e citá-lo na política
  de privacidade como operador.
- Pontos em aberto da apuração mensal e do IRPF (TODO.md), principalmente units
  tributadas sem a isenção de R$ 20 mil.
- Revisar `/privacidade` contra o que a aplicação faz de fato.
