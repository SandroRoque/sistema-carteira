# Deploy

A aplicação roda como **um** container no [Fly.io](https://fly.io) (região `gru`) com
PostgreSQL no [Neon](https://neon.tech) (região AWS São Paulo, `sa-east-1`). O worker de
importação é uma thread do próprio processo web; não há fila, storage ou serviço extra.

| Arquivo | Papel |
|---|---|
| `Dockerfile` | Imagem com as dependências de produção (sem testes nem dados locais, ver `.dockerignore`) |
| `fly.toml` | Máquina de 512 MB que para sem tráfego, checagem em `/saude`, migrações no `release_command` |
| `.github/workflows/ci.yml` | Testes e build da imagem em todo push para `main` e em PRs |
| `.github/workflows/deploy.yml` | Deploy manual (botão *Run workflow* no GitHub) |

## Variáveis

| Variável | Onde | Observação |
|---|---|---|
| `DATABASE_URL` | secret | Endpoint **direto** do Neon (ver abaixo) |
| `CPF_HMAC_KEY` | secret | Mínimo 32 caracteres. Guarde uma cópia fora do Fly: perder ou trocar a chave faz novos envios não reconhecerem as carteiras existentes |
| `CADASTRO_ABERTO` | `fly.toml` | `false` até existirem recuperação de senha e limite por IP (TODO.md) |
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

O `release_command` roda `alembic upgrade head` antes de cada versão entrar no ar; se a
migração falhar, a versão anterior continua servindo.

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

- Recuperação de senha e verificação de e-mail (provedor de e-mail transacional).
- Limite de tentativas de login por IP.
- Pontos em aberto da apuração mensal (TODO.md), principalmente units e ETFs
  classificados como FII.
- Revisar `/privacidade` contra o que a aplicação faz de fato.
