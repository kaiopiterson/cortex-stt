# Segurança

Não versionar segredos, áudio, transcrições ou configuração real.
Use TLS, acesso privado à administração e bloqueie /admin, /metrics e
/v1/admin/* no ingress público. Confie em CF-Connecting-IP somente se o
proxy for confiável e não houver acesso direto.
Chaves por aplicativo e tokens opacos de 900s são armazenados como hash.
A chave permanente permanece no backend confiável, nunca no APK.
O backend consumidor autentica seus usuários antes de emitir tokens.
Rotação invalida tokens e chave anterior; revogação bloqueia novas
autenticações e sessões WS são revalidadas. Inferência nativa em curso
não é cancelada.
Escopos: a chave permanente (stt_) só troca por token em /v1/auth/token;
REST e WebSocket aceitam apenas token (tok_) ou a chave legada.
Chave legada (STT_API_KEY): desligada por padrão em production
(STT_LEGACY_API_KEY_ENABLED). Quando ligada, é tratada como o aplicativo
interno "legacy": um único bucket de rate limit, cotas e concorrência
compartilhados por todos os clientes legados. Rotacionar STT_API_KEY não
zera esses limites.
Admin usa segredo independente, sem cookies ou armazenamento local.
Cotas agregadas persistem em SQLite; proteja volume e backups.
Mutações administrativas ficam em admin_audit (retenção de 400 dias).
Prometheus usa STT_METRICS_TOKEN, sem poderes administrativos.
Admin e métricas só respondem a STT_ADMIN_ALLOWED_NETWORKS; atrás do túnel,
com STT_TRUST_PROXY_HEADERS, o IP considerado é o do visitante. Sem confiar no
proxy, qualquer requisição com CF-Connecting-IP é tratada como externa.
Não exponha publicamente antes de validar CI, dependências e carga.
Reporte vulnerabilidades privadamente ao mantenedor.
