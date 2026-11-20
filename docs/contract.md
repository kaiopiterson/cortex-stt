# Contrato comum de transcrição — v0.2

REST e eventos partial/final acrescentam confidence e needs_review.
processing.location já existe no servidor e admite device no SDK futuro.
Não há roteamento híbrido implementado.

Confidence: média aritmética dos segmentos textuais com valores finitos,
de exp(min(0, avg_logprob)) × (1 − clamp(no_speech_prob, 0, 1)).
Sem segmentos válidos, zero. No streaming o valor descreve a última janela,
não a confiança calibrada de todo o histórico.
needs_review é verdadeiro para texto vazio, confidence abaixo de
STT_REVIEW_THRESHOLD (0,6) ou descarte por backlog no streaming.
Não usar confidence como probabilidade calibrada de acerto.

Hotwords: array JSON de até 50 strings não vazias de até 64 caracteres.
REST: campo multipart hotwords contendo o JSON; WS: array no handshake.
O servidor concatena os termos com espaços e repassa ao faster-whisper.
São sugestões, não restrições. Protocolo 1.1 permanece compatível.

JSON Schema (núcleo comum, campos adicionais permitidos):
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": [
    "text",
    "confidence",
    "needs_review",
    "processing"
  ],
  "properties": {
    "text": {
      "type": "string"
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "needs_review": {
      "type": "boolean"
    },
    "processing": {
      "type": "object",
      "required": [
        "location"
      ],
      "properties": {
        "location": {
          "enum": [
            "server",
            "device"
          ]
        }
      }
    }
  }
}
```
