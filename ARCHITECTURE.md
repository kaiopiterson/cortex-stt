# Arquitetura
Aplicações -> SDK universal -> roteador adaptativo -> motor local ou servidor -> resultado comum.
Modos planejados: server, edge-lite, offline, hybrid, auto. Offline nunca envia áudio. Nenhum dispositivo processa áudio de terceiros. Servidor FastAPI/Faster-Whisper/CTranslate2; edge avalia whisper.cpp e ONNX. Módulos lógicos, sem microsserviços prematuros. Preservar REST e versionar protocolo WebSocket. Ritmo interpreta o texto e solicita confirmação antes de criar rotinas; STT não executa ações nem interpreta intenções.
Atualmente somente motor servidor MVP está implementado. Planejar contracts/, edge/, sdk/, integrations/, benchmarks/ e infrastructure/ conforme roadmap.

## Servidor v0.3
FastAPI -> autenticação -> identidade app -> cotas SQLite e admissão -> fila prioritária -> CTranslate2.
SQLite WAL mantém registro, hashes e buckets UTC. Concorrência e métricas são locais ao processo; um worker.
Admin separado com Bearer; dashboard consulta métricas e consumo agregado.
Edge e SDK ainda planejados.
