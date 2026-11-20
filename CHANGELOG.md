# Histórico
## v0.1.0 — MVP
- Motor central Faster-Whisper, REST, WebSocket protótipo, interface web, Docker Compose e benchmark REST.
- Roadmap para SDK, edge e processamento híbrido.
- Validação operacional GPU, testes de carga e segurança de produção pendentes.

## Unreleased — v0.2 hardening

- Pin critical inference dependencies and smoke-test imports.
- Bound decoded audio duration and separate REST/stream admission.
- Rate limit by credential hash with opt-in trusted proxy headers.
- Add configurable stream connection limits and busy warnings.
- Validate languages, limit uploads and hide API docs in production.
- Add bounded streaming receiver queue and mock tests.
- WebSocket warning event is additive; protocol version remains 1.1.
- Validation, full dependency lock and performance benchmarks pending.

## v0.3.0 — Release candidate
- Registro SQLite, tokens, rotação, revogação, cotas e concorrência por app.
- Métricas Prometheus, NVML opcional, auditoria agregada e dashboard.
- Homologação, locks e benchmark GPU pendentes.

### v0.3.0 — correções da auditoria (Onda 1)
- AUD-01: emissão de tokens corrigida.
- AUD-02: admin autenticado antes do parsing; limite global de corpo.
- AUD-05: escopos de credencial; chave permanente não transcreve (403).
- AUD-04: chave legada atrás de STT_LEGACY_API_KEY_ENABLED e contabilizada
  como app "legacy" (bucket, cota e concorrência compartilhados). Mudança de
  comportamento: clientes legados deixam de ter buckets por hash de chave.
- AUD-06/13: sessão WS sobrevive à expiração do token; cota esgotada envia final.
- AUD-08: limites de emissão por app e de falhas por IP; compose.tunnel.yaml.
- AUD-09: dependências corrigidas com lock versionado e hashes.
- AUD-03: workflow de CI voltou a ser YAML válido; lint e tipos zerados.

### v0.3.0 — correções da auditoria (Onda 2)
- AUD-07: limites por app no banco e por sujeito do token (subject); classes
  de prioridade interactive/normal/batch; CLI `limits` e PUT /v1/admin/apps/{id}/limits.
  Novos padrões por app: 600 req/min, 10 h/dia, 200 h/mês, 8 simultâneas.
- AUD-12: ledger de consumo em memória com flush em lote; cache de identidade;
  indisponibilidade do banco vira 503 sem derrubar sessões.
- AUD-18: estorno do áudio não processado em falha do servidor.
- AUD-14: retenção automática e backup/restauração online (app.db_backup).
- AUD-10/11: STT_METRICS_TOKEN somente leitura, STT_ADMIN_ALLOWED_NETWORKS,
  trilha admin_audit. Corrige o painel /admin, que não abria no navegador.
- AUD-15: prometheus_client, logs JSON com request_id, perfil Compose
  `observability` (Prometheus + Grafana com dashboard e alertas).
- AUD-16: fallback int8_float16 → int8_float32 → CPU, métrica stt_degraded;
  STT_WORKERS padrão 1.
- AUD-17: filtro de alucinações (no_speech/logprob e frases típicas PT-BR).
- AUD-19: interface web com AudioWorklet. AUD-20: /ready e /health mínimo.
- AUD-21: prepare_audio público, uma consulta de identidade por requisição.
- AUD-22: imagens base por digest; Trivy (aquasec/trivy:0.72.0) no CI.
- Pendente de validação real: GPU GTX 1650, benchmarks de latência, WER/CER e
  calibração do filtro de alucinações.
