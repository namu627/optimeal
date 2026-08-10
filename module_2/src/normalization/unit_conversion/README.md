# 비정형단위 환산 테이블 파이프라인

식품안전나라 레시피 원문의 비정형 단위 표기("양파 200g(1개)", "간장 15g(큰술)")를 파싱해
**재료 × 단위 → 환산 중량(g)** 테이블을 만드는 7단계 파이프라인.

- **최종 산출물**: `비정형단위_환산_테이블_최종.xlsx` (207행) — 이 폴더에 **단일 원본**으로 둔다.
  `.gitignore`가 `*.xlsx`를 무시하므로 `git add -f`로 추적한다(`data/raw/*.xlsx` 3건과 동일한 선례).
  `with_confidence.csv`(추적됨)에서 `step5`+`step7`로 **재생성 가능**하다.
- **담당**: 권성민 · **이관**: 2026-08-10 (`feature/ksm` → develop)
- **연계**: FR-01 비정형 레시피 정규화 / DB `unit_conversion` 테이블 (ADR: 단위 환산은 테이블 기반 결정론 처리, LLM 확률 출력 금지)

## 왜 이 위치인가

원래 `feature/ksm` 브랜치 루트의 `Converting Unstructured Data/`에 있었다. 그 브랜치는 develop과
**공통 조상이 없고 `venv/` 2,368파일이 커밋돼 있어** 브랜치째 병합할 수 없었으므로, 이 산출물만
정규화 모듈 아래로 파일 단위 이관했다.

스크립트가 상대경로(`open('RCP_PARTS_DTLS.json')`)로 입출력하므로 **원본 입력·중간 산출물을 이 폴더에
함께 보존**한다. 경로를 갈라 놓으면 스크립트가 그대로는 돌지 않기 때문이다.

## 실행

```bash
cd module_2/src/normalization/unit_conversion   # ← cwd 고정 필요 (상대경로 사용)
python step2_extract.py          # 원문 파싱 → extracted_raw.csv
python step2_parse_units.py      # 단위 표기 정규화 → extracted_parsed.csv
python step2_calculate_convwt.py # 환산 중량 산출 → extracted_with_convwt.csv
python step3_check_duplicates.py # 중복 진단 → duplicates_report.csv
python step3_merge_duplicates.py # 중복 병합 → merged_data.csv
python step4_calculate_tolerance.py  # 허용 오차율 → with_tolerance.csv
python step4_add_confidence.py       # 신뢰도 등급 → with_confidence.csv
python step5_create_final_table.py   # 최종 테이블 생성
python step5_validate.py             # 검증
python step7_visualize_excel.py      # xlsx 서식·시각화
python step6_test_conversion.py      # 환산 조회 스모크
```

`step1_explore.py`는 원본 JSON 구조 탐색용(1회성).

## 최종 테이블 스키마

| 컬럼 | 의미 |
|---|---|
| `INGR_NM` | 재료명 |
| `UNIT_NM` | 비정형 단위 (개·큰술·작은술·컵 등) |
| `CONV_WT` | 환산 중량 (g) |
| `CONDITION_DESC` | 조건 설명 (예: 중간 크기) — **현재 전 행 비어 있음, 수작업 보완 대상** |
| `TOLERANCE_PCT` | 허용 오차율 (%) — 샘플 간 산포 |
| `CONFIDENCE_LVL` | 신뢰도 1~3 (샘플 수·산포 기반) |
| `SAMPLE_COUNT` | 근거 샘플 수 |

## 알려진 한계

1. **샘플 수가 적다** — 다수 행이 `SAMPLE_COUNT` 2~4. `TOLERANCE_PCT`가 100%에 달하는 행도 있어
   (예: 가지/개) 그 조합은 환산 신뢰도가 낮다. `CONFIDENCE_LVL`로 걸러 쓸 것.
2. `CONDITION_DESC`가 전부 비어 있어 "큰 것/중간 것" 구분이 반영되지 않았다.
3. DB `unit_conversion` 테이블에는 **아직 적재하지 않았다.** 적재 시 `minimum_order_unit`·
   `reference_source` 컬럼 값을 함께 정해야 한다(미결정).
