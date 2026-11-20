# API Gateway v0.3 — primeiro incremento

GW-01, GW-02, GW-05: cadastro local, chaves individuais e SQLite WAL.
O volume registry em /data guarda o banco. Faça backup consistente com
SQLite backup API, não copie apenas o arquivo principal enquanto houver WAL.
Não use docker compose down -v para atualizar.

## Administração local

docker compose exec stt python3 -m app.registry_cli register Ritmo

docker compose exec stt python3 -m app.registry_cli list

A chave é exibida somente na criação; guarde em um gerenciador de segredos.
Não grave a saída em logs nem distribua chaves permanentes em aplicativos públicos.
A chave STT_API_KEY antiga continua funcionando. O REST usa Authorization Bearer
e o WebSocket usa api_key no handshake 1.1. Nenhum endpoint administrativo público.

## Limitações

Auditoria e métricas ainda não implementadas. O rate limit existente continua por hash de credencial.
O banco deve permanecer em armazenamento local, não em compartilhamento de rede.
Não há migração versionada de schema neste incremento.

## Tokens e revogação

POST /v1/auth/token com Authorization: Bearer <chave individual> retorna
access_token, token_type e expires_in (900 segundos). Sem refresh token.
A chave legada e tokens de acesso não podem emitir tokens.
Use o token no Bearer REST ou api_key do handshake WebSocket.
Emissão limitada por IP; limites de consumo por app ainda pendentes.
O backend consumidor deve autenticar e autorizar seus usuários antes de emitir tokens.

CLI local: rotate APP_ID, revoke APP_ID, revoke-tokens APP_ID.
Rotação invalida a chave anterior e todos os tokens do app. Revogação do app
é definitiva neste incremento. Sessões WebSocket revalidam credenciais entre mensagens, durante espera ociosa a cada segundo e antes de publicar resultados. Inferência nativa em andamento não é interrompida.
As tabelas adicionais são criadas ao iniciar, preservando os registros antigos.

## Governança de consumo

Cada identidade é medida em dois escopos (AUD-07):

| Escopo | Chave | Limites |
|---|---|---|
| Aplicativo | `app_id` | por app no banco; padrão `STT_APP_*` |
| Sujeito | `app_id#subject` | `STT_SUBJECT_*` (usuário ou dispositivo do token) |

Padrões do app: 600 requisições/min, 36000 s/dia, 720000 s/mês, 8 operações
simultâneas. Padrões por sujeito: 60 requisições/min, 3600 s/dia, 36000 s/mês,
2 operações simultâneas. Um sujeito que esgota a cota não bloqueia os demais.

O backend emite o token com o sujeito (id opaco, `[A-Za-z0-9._-]{1,64}`):

    POST /v1/auth/token  {"subject": "device-42"}

Limites por app (override; `default` volta ao padrão do ambiente):

    python3 -m app.registry_cli limits APP_ID --rpm 1200 --concurrency 4 --priority interactive
    PUT /v1/admin/apps/{app_id}/limits  {"concurrency": 4, "priority_class": "interactive"}

Classe de prioridade (`interactive`, `normal`, `batch`) ordena a fila de
inferência antes do modo: um app `batch` nunca passa à frente de trabalho
`normal`. Comandos e utterances vêm antes de ditado dentro da mesma classe.

Contabilização (AUD-12): limites são verificados em memória e o consumo é
gravado no SQLite em lote a cada `STT_USAGE_FLUSH_SECONDS` (padrão 2 s) e no
desligamento. Credenciais são consultadas uma vez por segundo no máximo
(cache), e revogação/rotação feitas pelo admin valem na hora; pela CLI, em até
1 s. Indisponibilidade do banco retorna 503 sem derrubar sessões ativas.

Estorno (AUD-18): áudio cobrado e não processado por falha do servidor
(fila cheia, modelo indisponível, erro de inferência) é devolvido à cota.

Emissão de tokens: `STT_TOKEN_ISSUANCE_PER_MINUTE` por app (padrão 600) e
`STT_TOKEN_FAILURES_PER_MINUTE` por IP para credenciais inválidas (padrão 30).

A chave legada é o app interno `legacy` (ver SECURITY.md).
REST retorna 429 e WS `quota_exceeded`/1008 quando um limite é excedido.
Contadores e concorrência são locais ao processo: use um único worker Uvicorn.
Rate limit usa janela fixa UTC, podendo permitir rajadas na virada do minuto.

## Observabilidade

Consulte [observability.md](observability.md) para coleta protegida de métricas e auditoria agregada.
