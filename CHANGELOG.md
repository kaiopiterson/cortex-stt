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
