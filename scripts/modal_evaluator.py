import modal

app = modal.App("qwen-72b-evaluator")  # New name to ensure a fresh start

vllm_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "vllm", "huggingface_hub", "hf_transfer", "fastapi", "uvicorn", "numpy<2"
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

MODEL_NAME = "Qwen/Qwen2.5-72B-Instruct-AWQ"


@app.function(
    image=vllm_image,
    gpu="A100-80GB",
    timeout=3600,
    min_containers=0,  # Set to 0 so it only runs when we tell it to
)
@modal.web_server(port=8000, startup_timeout=1000)
def serve():
    import subprocess

    print(f"Starting vLLM Judge: {MODEL_NAME}...")
    subprocess.Popen(
        [
            "python",
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            MODEL_NAME,
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--dtype",
            "bfloat16",
            "--gpu-memory-utilization",
            "0.90",
            "--max-model-len",
            "16384",
        ]
    )
