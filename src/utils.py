"""bbox / 위치 정보 헬퍼 유틸리티."""

from __future__ import annotations

import cv2
import numpy as np
from docling_core.types.doc import PictureItem, TableItem


def get_location(element, doc) -> dict | None:
    """element의 위치 정보를 딕셔너리로 반환.

    Returns:
        page_no, page_w, page_h, bbox_raw(BOTTOMLEFT), bbox_tl(TOPLEFT)
    """
    if not element.prov:
        return None
    p = element.prov[0]
    page = doc.pages.get(p.page_no)
    if page is None:
        return None
    bbox_tl = p.bbox.to_top_left_origin(page.size.height)
    return {
        "page_no": p.page_no,
        "page_w": page.size.width,
        "page_h": page.size.height,
        "bbox_raw": {"l": p.bbox.l, "t": p.bbox.t, "r": p.bbox.r, "b": p.bbox.b},
        "bbox_tl": {"l": bbox_tl.l, "t": bbox_tl.t, "r": bbox_tl.r, "b": bbox_tl.b},
    }


def get_bbox_str(element, doc) -> tuple[str, str]:
    """element의 (page_no 문자열, bbox 문자열) 튜플 반환."""
    loc = get_location(element, doc)
    if not loc:
        return "", ""
    bb = loc["bbox_tl"]
    return str(loc["page_no"]), f'l={bb["l"]:.1f} t={bb["t"]:.1f} r={bb["r"]:.1f} b={bb["b"]:.1f}'


def get_figure_category(element) -> str:
    """PictureItem의 분류 카테고리 반환."""
    for ann in element.get_annotations():
        if hasattr(ann, "predicted_classes") and ann.predicted_classes:
            return ann.predicted_classes[0].class_name
    return "unknown"


def draw_bboxes_on_page(doc, page_no: int, elements: list) -> np.ndarray:
    """특정 페이지 이미지에 여러 element의 bounding box를 그려서 반환.

    Args:
        doc: Docling 변환 결과 document
        page_no: 시각화할 페이지 번호 (1-based)
        elements: (element, label, color) 튜플 리스트

    Returns:
        OpenCV BGR 이미지 (numpy array)

    좌표 변환 원리:
        bbox_tl 좌표는 pt 단위, page.size도 pt 단위.
        실제 픽셀 스케일 = 이미지 실제 픽셀 크기 / page.size(pt)
        IMAGE_SCALE 상수를 직접 곱하면 Docling 내부 반올림 오차로 얼라인이 틀어지므로
        반드시 실제 이미지 픽셀 크기 기준으로 스케일을 계산해야 함.
    """
    # Docling이 생성한 페이지 이미지(PIL) → OpenCV BGR 배열로 변환
    page_img_pil = doc.pages[page_no].image.pil_image
    img = cv2.cvtColor(np.array(page_img_pil), cv2.COLOR_RGB2BGR)
    img_w, img_h = page_img_pil.size  # 실제 픽셀 크기

    # 페이지 pt 크기 → 실제 픽셀 스케일 계산 (x/y 축 각각)
    page = doc.pages[page_no]
    scale_x = img_w / page.size.width
    scale_y = img_h / page.size.height

    for element, label, color in elements:
        loc = get_location(element, doc)
        if loc is None or loc["page_no"] != page_no:
            continue
        bb = loc["bbox_tl"]  # 좌상단 원점 기준 pt 좌표

        # pt → 픽셀 변환: x/y 스케일 각각 적용
        x1, y1 = int(bb["l"] * scale_x), int(bb["t"] * scale_y)
        x2, y2 = int(bb["r"] * scale_x), int(bb["b"] * scale_y)

        cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness=2)
        # 라벨 텍스트 (박스 위쪽에 배경 포함하여 가독성 확보)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(
            img,
            label,
            (x1 + 2, y1 - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    return img


def generate_bbox_images(parsed_doc, output_dir) -> dict[int, bytes]:
    """모든 페이지의 바운딩 박스 시각화 이미지를 생성.

    Args:
        parsed_doc: ParsedDocument 객체
        output_dir: 출력 디렉토리 (저장용, 현재는 저장하지 않음)

    Returns:
        {page_no: jpg_bytes} 딕셔너리
    """
    from collections import defaultdict

    COLOR_PICTURE = (0, 200, 0)  # 초록 - 그림(figure)
    COLOR_TABLE = (0, 0, 220)  # 빨강 - 테이블

    # 페이지별로 element 수집
    page_elements: dict[int, list] = defaultdict(list)

    for element, _level in parsed_doc.doc.iterate_items():
        if isinstance(element, PictureItem):
            # 분류 카테고리를 라벨로 사용
            label = get_figure_category(element)[:12]  # 너무 길면 잘라냄
            loc = get_location(element, parsed_doc.doc)
            if loc:
                page_elements[loc["page_no"]].append((element, label, COLOR_PICTURE))

        elif isinstance(element, TableItem):
            loc = get_location(element, parsed_doc.doc)
            if loc:
                page_elements[loc["page_no"]].append((element, "table", COLOR_TABLE))

    # 각 페이지 시각화 및 JPEG 바이트로 인코딩
    bbox_images = {}
    for page_no, elems in sorted(page_elements.items()):
        img = draw_bboxes_on_page(parsed_doc.doc, page_no, elems)
        # JPEG로 인코딩 (Streamlit 표시용)
        _, jpg_buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        bbox_images[page_no] = jpg_buffer.tobytes()

    return bbox_images
