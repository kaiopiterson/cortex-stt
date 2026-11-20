"""Import smoke test; never loads a model."""
def test_runtime_imports():
    import av
    import ctranslate2
    import faster_whisper
    import huggingface_hub
    import onnxruntime
    import requests
    import tokenizers

    assert all((av, ctranslate2, faster_whisper, huggingface_hub,
                onnxruntime, requests, tokenizers))
