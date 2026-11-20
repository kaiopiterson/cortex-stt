# Roadmap
- v0.1 Foundation MVP: REST, WebSocket protótipo, Faster-Whisper, Docker, interface e benchmark REST; validação GPU pendente.
- v0.2 Ultra Low Latency: segmentação incremental, CUDA, AudioWorklet, sessões longas controladas, benchmarks WebSocket, WER/CER e latência.
- v0.3 API Gateway: chaves individuais, tokens temporários com sujeito, rate limiting, cotas por app e por sujeito, auditoria, Prometheus e Grafana. Implementado e auditado; homologação em GPU pendente.
- v0.4 Universal SDK: Python, TypeScript, Android e Windows; contrato comum, reconexão e integração piloto com Ritmo.
- v0.5 Edge Lite: VAD, reamostragem, compressão e segmentação no dispositivo.
- v0.6 Edge AI Offline: avaliação whisper.cpp, ONNX e Sherpa-ONNX; modelos quantizados, integridade, bateria e temperatura.
- v0.7 Adaptive Hybrid: roteamento por capacidade, conectividade, privacidade e qualidade; reprocessamento seletivo e deduplicação.
- v0.8 Ecosystem: Ritmo, Voice Studio, clientes independentes, web e avaliação iOS.
- v1.0 Production: segurança, compatibilidade, observabilidade, testes de carga, documentação e escalabilidade comprovadas.

Metas aspiracionais: primeiro parcial em 500 ms, final de ditado curto em até 1 s após fala, 4–10 sessões concorrentes; confirmar por benchmarks P50/P95/P99, RTF, WER/CER e consumo.

## v0.3 RC
GW-01 a GW-11: implementação inicial publicada; GW-12/13 em validação.
Pendências: CI verde, locks, auditoria granular, cotas editáveis, benchmark GPU.
AudioWorklet e WER/CER da v0.2 permanecem pendentes.
