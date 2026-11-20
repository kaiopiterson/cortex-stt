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

Tokens temporários, revogação, cotas, auditoria, métricas e rotação ainda não
implementados. O rate limit existente continua por hash de credencial.
O banco deve permanecer em armazenamento local, não em compartilhamento de rede.
Não há migração versionada de schema neste incremento.
