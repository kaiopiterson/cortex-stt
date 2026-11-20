# Arquitetura
Aplicações -> SDK universal -> roteador adaptativo -> motor local ou servidor -> resultado comum.
Modos planejados: server, edge-lite, offline, hybrid, auto. Offline nunca envia áudio. Nenhum dispositivo processa áudio de terceiros. Servidor FastAPI/Faster-Whisper/CTranslate2; edge avalia whisper.cpp e ONNX. Módulos lógicos, sem microsserviços prematuros. Preservar REST e versionar protocolo WebSocket. Ritmo interpreta o texto e solicita confirmação antes de criar rotinas; STT não executa ações nem interpreta intenções.
Atualmente somente motor servidor MVP está implementado. Planejar contracts/, edge/, sdk/, integrations/, benchmarks/ e infrastructure/ conforme roadmap.
