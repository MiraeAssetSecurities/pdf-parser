FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Python deps
RUN pip install --no-cache-dir \
    torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Clone dots.mocr repo and install deps (flash-attn removed for CPU)
RUN git clone --depth 1 https://github.com/rednote-hilab/dots.mocr.git /tmp/dots.mocr && \
    cd /tmp/dots.mocr && \
    sed -i '/flash-attn/d' requirements.txt && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf /tmp/dots.mocr/.git

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    python-multipart

# Download model weights (dots.mocr = latest version of dots.ocr)
RUN pip install --no-cache-dir huggingface_hub && \
    python3 -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('rednote-hilab/dots.mocr', local_dir='/app/weights/DotsMOCR')"

# Patch for CPU: conditional flash_attn import + force sdpa in vision config
RUN sed -i 's/from flash_attn import flash_attn_varlen_func/try:\n    from flash_attn import flash_attn_varlen_func\nexcept ImportError:\n    flash_attn_varlen_func = None/' /app/weights/DotsMOCR/modeling_dots_vision.py && \
    python3 -c "\
import json; \
f='/app/weights/DotsMOCR/config.json'; \
cfg=json.load(open(f)); \
cfg['vision_config']['attn_implementation']='sdpa'; \
json.dump(cfg,open(f,'w'),indent=2)"

# Copy server code
COPY server.py /app/server.py

EXPOSE 8080

CMD ["python3", "server.py"]
