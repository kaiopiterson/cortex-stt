# Arquitetura
Aplicações -> SDK universal -> roteador adaptativo -> motor local ou servidor -> resultado comum.
Modos planejados: server, edge-lite, offline, hybrid, auto. Offline nunca envia áudio. Nenhum dispositivo processa áudio de terceiros. Servidor FastAPI/Faster-Whisper/CTranslate2; edge avalia whisper.cpp e ONNX. Módulos lógicos, sem microsserviços prematuros. Preservar REST e versionar protocolo WebSocket. Ritmo interpreta o texto e solicita confirmação antes de criar rotinas; STT não executa ações nem interpreta intenções.
Atualmente somente motor servidor MVP está implementado. Planejar contracts/, edge/, sdk/, integrations/, benchmarks/ e infrastructure/ conforme roadmap.

## Servidor v0.3
RequestContext (request_id, log JSON, métricas HTTP) -> BodyLimit (autentica
admin/métricas e transcrição antes do corpo) -> rota -> Gateway (política por
app e por sujeito, rate limit, cotas, concorrência, prioridade) -> fila de
inferência com prioridade -> CTranslate2 (fallback de compute).

- app/core/registry.py: apps, hashes de chaves e tokens, limites por app,
  auditoria administrativa, migrações numeradas (schema_version).
- app/core/auth.py: identidade (app, tipo, sujeito) com cache de 1 s.
- app/core/gateway.py + usage.py: escopos app e app#sujeito; ledger em memória
  com flush em lote no SQLite WAL; estorno em falha do servidor.
- app/core/maintenance.py, app/db_backup.py: retenção e backup online.
- app/core/telemetry.py, observability.py: Prometheus e logs JSON.
- app/api/metrics.py: /metrics com token somente leitura; admin separado.

Estado em memória (ledger, vagas, rate limits) exige um único processo
Uvicorn. Escalar horizontalmente exigiria armazenamento compartilhado (fora do
escopo; o hardware alvo é uma GTX 1650). Edge e SDK ainda planejados.
