# Dashboard administrativo

Configure STT_ADMIN_KEY com segredo independente e acesse /admin localmente.
A página contém apenas a interface de login; dados e mutações exigem Bearer.
A chave administrativa fica em memória, sem cookies ou armazenamento local.
Credenciais emitidas aparecem na resposta de criação ou rotação; copie para
um gerenciador de segredos e oculte a chave. Não use HTTP fora do loopback.

Rotas: GET /admin, GET e POST /v1/admin/apps,
POST /v1/admin/apps/{id}/rotate, /revoke e /revoke-tokens.
GET /metrics e /v1/admin/usage alimentam o painel a cada 15 segundos.
Cotas são globais por identidade e somente leitura; altere STT_APP_* no
ambiente e reinicie de forma controlada. Revogação do app é definitiva.
Auditoria de consumo é agregada; não há trilha de mutações administrativas.
Bloqueie /admin, /metrics e /v1/admin/* no ingresso público; use acesso
privado de gerenciamento. O túnel não é alterado por este código.
Teste: python -m pytest tests/test_dashboard.py tests/test_admin.py -v.
