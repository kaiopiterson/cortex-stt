# Perfis CUDA — 4 GB de VRAM

Comece com small, int8_float16, um worker e beam_size=1.
Avalie dois workers somente após medir VRAM e P95.
large-v3-turbo com int8_float16 e um worker é experimental em 4 GB;
não há garantia de que caiba na GPU. Evite dois workers até medir.
A GTX 1650 pode não oferecer int8_float16 no CTranslate2; confira
get_supported_compute_types("cuda", 0). Se indisponível, use int8_float32.
CPU: int8. O arquivo .env existente não é alterado automaticamente.
NVML é opcional (pacote nvidia-ml-py); logs de VRAM são best effort
e o índice NVML pode diferir do índice CUDA em ambientes remapeados.
