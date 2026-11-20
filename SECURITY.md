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
não é cancelada. Chave legada preserva limites antigos.
Admin usa segredo independente, sem cookies ou armazenamento local.
Cotas agregadas persistem em SQLite; proteja volume e backups.
Retenção e auditoria de eventos administrativos ainda pendentes.
Não exponha publicamente antes de validar CI, dependências e carga.
Reporte vulnerabilidades privadamente ao mantenedor.
