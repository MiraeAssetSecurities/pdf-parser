# dots.ocr 평가 리포트

- 생성일시 : 2026-04-14 04:09
- 데이터셋 : funsd
- 샘플 수  : 20 페이지
- 엔드포인트: dots-mocr-vllm-endpoint
- IoU 기준  : 0.5

## 텍스트 정확도
| 지표 | 값 |
|------|----|
| CER (↓) | 0.6047 (60.5%) |
| WER (↓) | 0.5527 (55.3%) |
| NED (↓) | 0.4419 |

## 레이아웃 감지 (IoU ≥ 0.5)
| 지표 | 값 |
|------|----|
| Precision (↑) | 0.2381 |
| Recall    (↑) | 0.0223 |
| F1        (↑) | 0.0403 |
| Mean IoU  (↑) | 0.7215 |

## 카테고리 분류
| 지표 | 값 |
|------|----|
| Category Accuracy (↑) | 0.6092 |

## 산출물
- `summary.json`        : 수치 요약
- `metrics_chart.png`   : 지표 차트
- `confusion_matrix.png`: 카테고리 혼동 행렬