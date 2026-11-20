# Observabilidade v0.3

Configure STT_METRICS_TOKEN (somente leitura, ≥ 32 caracteres, distinto das
demais chaves) para o Prometheus. /metrics aceita esse token ou STT_ADMIN_KEY;
o token de métricas recebe 401 em todas as rotas /v1/admin/*. Sem nenhum dos
dois, /metrics responde 404. Acesso limitado a STT_ADMIN_ALLOWED_NETWORKS.
Não exponha essas rotas no túnel público: negue /metrics e /v1/admin/* no
ingress antes da regra geral do STT, ou use rede de gerenciamento privada.
Nenhuma regra de túnel é alterada automaticamente.

## Prometheus

Exemplo para Prometheus executado no host (ajuste target se estiver em container):

    scrape_configs:
      - job_name: cortex-stt
        metrics_path: /metrics
        scrape_interval: 15s
        authorization:
          type: Bearer
          credentials_file: /etc/prometheus/secrets/stt-metrics-token
        static_configs:
          - targets: ["127.0.0.1:8000"]

PromQL:

    histogram_quantile(0.95, sum by (le) (rate(stt_inference_duration_seconds_bucket[5m])))
    histogram_quantile(0.95, sum by (le) (rate(stt_queue_wait_seconds_bucket[5m])))
    stt_inference_queue_depth
    stt_gpu_memory_used_bytes / stt_gpu_memory_total_bytes

Histogramas medem janelas de inferência, não a latência fim a fim do cliente.
Contadores são locais ao processo e reiniciam com o serviço.
NVML é opcional (nvidia-ml-py, driver NVIDIA e acesso a NVML no container).
O índice NVML pode diferir do índice CUDA; confirme no servidor.
GPU indisponível não equivale a utilização zero.

## Auditoria

GET /v1/admin/usage?limit=100 retorna buckets persistentes de consumo
(limite máximo 1000), não uma trilha de eventos individuais. Dias e meses
contêm as mesmas amostras agregadas em períodos diferentes: não somá-los.
Não inclui áudio, texto, chaves ou tokens. Retenção e paginação ainda pendentes.
