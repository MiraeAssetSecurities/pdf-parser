#!/bin/bash
# dots.ocr Parser — 오프라인 패키지 설치 스크립트
# 사용: bash setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHEELS_DIR="$SCRIPT_DIR/wheels"

echo "========================================"
echo " dots.ocr Parser 패키지 설치"
echo "========================================"
echo "Python: $(python3 --version)"
echo "Wheels: $WHEELS_DIR"
echo ""

if [ ! -d "$WHEELS_DIR" ]; then
    echo "[오류] wheels/ 폴더가 없습니다: $WHEELS_DIR"
    exit 1
fi

python3 -m pip install \
    --no-index \
    --find-links "$WHEELS_DIR" \
    -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "========================================"
echo " 설치 완료!"
echo " Jupyter에서 dots_ocr_parser.ipynb 열어 실행하세요."
echo "========================================"
