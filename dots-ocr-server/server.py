"""dots.ocr CPU inference server - FastAPI REST API."""

import io
import json
import logging
import re
import time
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor
from qwen_vl_utils import process_vision_info

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
)
logger = logging.getLogger("dots-ocr-server")

MODEL_PATH = Path("/app/weights/DotsMOCR")

# --- Prompts from dots.ocr official repo ---
PROMPT_OCR = (
    "Extract texts from the image. For each detected text region, output a JSON "
    "object with the key 'text' containing the recognized text string. Output one "
    "JSON object per line."
)

PROMPT_LAYOUT = (
    "Parse the layout of the image. For each detected element, output a JSON object "
    "with keys: 'category' (one of: Title, Text, Section-header, List-item, Table, "
    "Picture, Caption, Formula, Footnote, Page-header, Page-footer), 'bbox' "
    "(normalized [x1,y1,x2,y2]), and 'text' (content). Tables should use HTML format. "
    "Formulas should use LaTeX format. Output one JSON object per line."
)

app = FastAPI(title="dots.ocr CPU Server", version="1.0.0")

model = None
processor = None


def load_model():
    """Load dots.ocr model for CPU inference."""
    global model, processor
    logger.info("Loading dots.ocr model from %s ...", MODEL_PATH)
    start = time.time()

    model = AutoModelForCausalLM.from_pretrained(
        str(MODEL_PATH),
        attn_implementation="sdpa",
        torch_dtype=torch.bfloat16,
        device_map="cpu",
        trust_remote_code=True,
    )
    processor = AutoProcessor.from_pretrained(
        str(MODEL_PATH), trust_remote_code=True
    )

    elapsed = time.time() - start
    logger.info("Model loaded in %.1fs", elapsed)


@app.on_event("startup")
async def startup():
    load_model()


@app.get("/health")
async def health():
    return {
        "status": "ok" if model is not None else "loading",
        "model": str(MODEL_PATH),
        "device": "cpu",
    }


def run_inference(image: Image.Image, prompt: str, max_tokens: int = 4096) -> str:
    """Run dots.ocr inference on a single image."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": prompt},
            ],
        }
    ]

    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    )

    start = time.time()
    with torch.no_grad():
        generated_ids = model.generate(**inputs, max_new_tokens=max_tokens)

    generated_ids_trimmed = [
        out_ids[len(in_ids) :]
        for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
    ]
    output_text = processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )[0]

    elapsed = time.time() - start
    logger.info("Inference done in %.1fs (%d tokens generated)", elapsed, len(generated_ids_trimmed[0]))
    return output_text


def parse_jsonl(text: str) -> list[dict]:
    """Parse newline-delimited JSON output from dots.ocr."""
    results = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            results.append({"raw": line})
    return results


@app.post("/ocr")
async def ocr(
    file: UploadFile = File(...),
    max_tokens: int = Form(4096),
):
    """Extract text from an image using dots.ocr."""
    image_bytes = await file.read()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    raw_output = run_inference(image, PROMPT_OCR, max_tokens)
    parsed = parse_jsonl(raw_output)

    return {
        "raw": raw_output,
        "results": parsed,
        "image_size": {"width": image.width, "height": image.height},
    }


@app.post("/layout")
async def layout(
    file: UploadFile = File(...),
    max_tokens: int = Form(4096),
):
    """Parse document layout from an image using dots.ocr."""
    image_bytes = await file.read()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    raw_output = run_inference(image, PROMPT_LAYOUT, max_tokens)
    parsed = parse_jsonl(raw_output)

    return {
        "raw": raw_output,
        "results": parsed,
        "image_size": {"width": image.width, "height": image.height},
    }


@app.post("/custom")
async def custom(
    file: UploadFile = File(...),
    prompt: str = Form(...),
    max_tokens: int = Form(4096),
):
    """Run custom prompt on an image."""
    image_bytes = await file.read()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    raw_output = run_inference(image, prompt, max_tokens)
    parsed = parse_jsonl(raw_output)

    return {
        "raw": raw_output,
        "results": parsed,
        "image_size": {"width": image.width, "height": image.height},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
