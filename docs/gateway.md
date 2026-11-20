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

Limites padrão por app: 60 admissões/minuto, 3600 segundos/dia UTC,
36000 segundos/mês UTC, duas operações REST/WS simultâneas.
Configuráveis via STT_APP_REQUESTS_PER_MINUTE, STT_APP_DAILY_AUDIO_SECONDS,
STT_APP_MONTHLY_AUDIO_SECONDS e STT_APP_CONCURRENCY.
Todos os tokens e chaves do mesmo app compartilham os buckets persistentes.
A chave legada mantém limites anteriores. Não é indicada para clientes públicos.
Áudio admitido é debitado antes de inferência; falhas posteriores não estornam.
REST retorna 429 e WS quota_exceeded/1008 quando limite excedido.
Concorrência é local ao processo único; não habilitar múltiplos workers Uvicorn.
Rate limit usa janela fixa UTC, podendo permitir rajadas na virada do minuto.
