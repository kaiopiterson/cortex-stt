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
