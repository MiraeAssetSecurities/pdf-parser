"""IBM docling-ibm-models LayoutPredictor 기반 직접 OCR 변환기.

Docling 전체 파이프라인 대신 LayoutPredictor를 직접 사용하여
더 세밀한 레이아웃 감지(Section-header, List-item, Key-Value Region 등)를 수행한다.

텍스트 추출은 PyMuPDF(fitz)를 사용한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import fitz  # PyMuPDF
import numpy as np
from PIL import Image

logger = logging.getLogger("pdf_parser.ibm_converter")

# LayoutPredictor 레이블 → OpenCV BGR 색상
_LABEL_COLORS_BGR: dict[str, tuple[int, int, int]] = {
    "Table":               (60,  60, 255),   # 빨강
    "Table rotated":       (0,  140, 255),   # 주황
    "Picture":             (50, 200,  50),   # 초록
    "Figure":              (50, 200,  50),   # 초록
    "Text":                (255, 130,  70),  # 파랑
    "Title":               (220,   0, 160),  # 보라
    "Section-header":      (255,  80, 180),  # 연보라
    "List-item":           (195, 195,   0),  # 청록
    "Formula":             (147,  20, 255),  # 핑크
    "Code":                (0,   128, 128),  # 올리브
    "Page-header":         (160, 160, 160),  # 회색
    "Page-footer":         (100, 100, 100),  # 진회색
    "Caption":             (0,   200, 255),  # 골드
    "Footnote":            (45,   82, 160),  # 갈색
    "Key-Value Region":    (0,   100, 200),  # 갈주황
}
_DEFAULT_COLOR_BGR: tuple[int, int, int] = (180, 180, 180)

_TABLE_LABELS = {"Table", "Table rotated"}
_FIGURE_LABELS = {"Picture", "Figure"}

DEFAULT_LAYOUT_MODEL_REPO = "ds4sd/docling-layout-egret-large"


class IbmLayoutConverter:
    """IBM docling-ibm-models LayoutPredictor를 직접 사용하는 변환기."""

    def __init__(
        self,
        layout_model_repo: str = DEFAULT_LAYOUT_MODEL_REPO,
        device: str = "auto",
        num_threads: int = 4,
        dpi: int = 150,
        base_threshold: float = 0.1,
    ):
        """
        Args:
            layout_model_repo: HuggingFace 모델 리포지토리 ID
            device: "auto" | "cpu" | "cuda" — auto는 CUDA 가용 시 자동 선택
            num_threads: CPU 병렬 처리 스레드 수
            dpi: PDF 페이지 이미지 변환 해상도
            base_threshold: LayoutPredictor confidence 임계값
        """
        import torch
        from docling_ibm_models.layoutmodel.layout_predictor import LayoutPredictor
        from huggingface_hub import snapshot_download

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info("Loading IBM layout model: %s (device=%s)", layout_model_repo, device)
        artifact_path = snapshot_download(repo_id=layout_model_repo)
        logger.info("Model artifact path: %s", artifact_path)

        self._predictor = LayoutPredictor(
            artifact_path=artifact_path,
            device=device,
            num_threads=num_threads,
            base_threshold=base_threshold,
        )
        self.dpi = dpi
        self.base_threshold = base_threshold
        logger.info(
            "IbmLayoutConverter ready (dpi=%d, threshold=%.2f)",
            dpi, base_threshold,
        )

    def convert(self, pdf_path: str | Path) -> "IbmParsedDocument":
        """PDF를 변환하여 IbmParsedDocument 반환.

        1. PyMuPDF로 페이지 이미지 및 텍스트 추출
        2. LayoutPredictor로 레이아웃 감지
        """
        pdf_path = Path(pdf_path)
        doc_name = pdf_path.stem

        # 1) PyMuPDF: 페이지 이미지 + 텍스트 추출
        logger.info("Converting PDF pages to images (DPI=%d): %s", self.dpi, pdf_path.name)
        scale = self.dpi / 72.0
        mat = fitz.Matrix(scale, scale)

        fitz_doc = fitz.open(str(pdf_path))
        page_images: list[Image.Image] = []
        page_texts: list[str] = []

        for page_num in range(len(fitz_doc)):
            page = fitz_doc[page_num]
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            page_images.append(img)
            page_texts.append(page.get_text())

        fitz_doc.close()
        logger.info("Converted %d pages", len(page_images))

        # 2) LayoutPredictor: 레이아웃 감지
        logger.info("Running layout prediction on %d pages", len(page_images))
        all_predictions: list[list[dict]] = []
        for i, img in enumerate(page_images):
            preds = list(self._predictor.predict(img))
            all_predictions.append(preds)
            logger.debug(
                "  Page %d: %d elements detected",
                i + 1, len([p for p in preds if p["confidence"] >= self.base_threshold]),
            )

        return IbmParsedDocument(
            doc_name=doc_name,
            page_images=page_images,
            all_predictions=all_predictions,
            page_texts=page_texts,
            confidence_threshold=self.base_threshold,
        )


class IbmParsedDocument:
    """IBM LayoutPredictor 변환 결과를 담는 문서 객체."""

    def __init__(
        self,
        doc_name: str,
        page_images: list[Image.Image],
        all_predictions: list[list[dict]],
        page_texts: list[str],
        confidence_threshold: float = 0.3,
    ):
        self.doc_name = doc_name
        self.page_images = page_images
        self.all_predictions = all_predictions
        self.page_texts = page_texts
        self.confidence_threshold = confidence_threshold

    def get_page_count(self) -> int:
        return len(self.page_images)

    def get_figures(self) -> list[dict]:
        """confidence >= threshold인 Picture/Figure 예측 목록 반환 (page_no 포함)."""
        result = []
        for page_no, preds in enumerate(self.all_predictions, start=1):
            for p in preds:
                if p["label"] in _FIGURE_LABELS and p["confidence"] >= self.confidence_threshold:
                    result.append({"page_no": page_no, **p})
        return result

    def get_tables(self) -> list[dict]:
        """confidence >= threshold인 Table 예측 목록 반환 (page_no 포함)."""
        result = []
        for page_no, preds in enumerate(self.all_predictions, start=1):
            for p in preds:
                if p["label"] in _TABLE_LABELS and p["confidence"] >= self.confidence_threshold:
                    result.append({"page_no": page_no, **p})
        return result

    def export_text_markdown(self) -> str:
        """PyMuPDF로 추출한 페이지별 텍스트를 마크다운 형식으로 반환."""
        parts = []
        for page_no, text in enumerate(self.page_texts, start=1):
            stripped = text.strip()
            if stripped:
                parts.append(f"## Page {page_no}\n\n{stripped}")
        return "\n\n---\n\n".join(parts)

    def generate_bbox_images(self, display_threshold: float | None = None) -> dict[int, bytes]:
        """모든 페이지에 IBM 레이아웃 bbox를 그린 JPEG 이미지를 반환.

        Args:
            display_threshold: bbox를 그릴 최소 confidence 값.
                None이면 self.confidence_threshold(기본 0.3) 사용.
                0.1~1.0 범위에서 낮을수록 더 많은 bbox 표시.

        Returns:
            {page_no: jpg_bytes} 딕셔너리 (1-based)
        """
        threshold = display_threshold if display_threshold is not None else self.confidence_threshold
        bbox_images: dict[int, bytes] = {}

        for page_no, (img, preds) in enumerate(
            zip(self.page_images, self.all_predictions), start=1
        ):
            cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

            # confidence 낮은 순으로 그려서 높은 것이 위에 오도록
            for pred in sorted(preds, key=lambda p: p["confidence"]):
                if pred["confidence"] < threshold:
                    continue

                label = pred["label"]
                conf = pred["confidence"]
                l, t, r, b = int(pred["l"]), int(pred["t"]), int(pred["r"]), int(pred["b"])
                color = _LABEL_COLORS_BGR.get(label, _DEFAULT_COLOR_BGR)

                cv2.rectangle(cv_img, (l, t), (r, b), color, thickness=2)

                text = f"{label[:14]} {conf:.2f}"
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                label_y = t - th - 6 if t > th + 6 else b + 2
                cv2.rectangle(cv_img, (l, label_y), (l + tw + 4, label_y + th + 4), color, -1)
                cv2.putText(
                    cv_img, text, (l + 2, label_y + th),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA,
                )

            _, jpg_buffer = cv2.imencode(".jpg", cv_img, [cv2.IMWRITE_JPEG_QUALITY, 90])
            bbox_images[page_no] = jpg_buffer.tobytes()

        return bbox_images
