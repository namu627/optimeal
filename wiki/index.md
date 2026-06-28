# OptiMeal Wiki — 인덱스

> LLM이 유지하는 지식 합성 레이어. raw source는 `docs/`를 읽을 것.
> 마지막 업데이트: 2026-06-28 (ADR-008 승인 확정 — NFR-01 재정의 반영)

---

## Decisions — ADR 결정 요약

| 페이지 | 한 줄 요약 |
|--------|-----------|
| [[decisions/adr_001_ratio]] | ratio = Y/(base×N) 종속변수 정의, 역변환 수식 확정 |
| [[decisions/adr_002_mixedlm]] | 3대분류 병합 + MixedLM 주전략, 가중 Jaccard 제거(v5) |
| [[decisions/adr_003_clipping]] | 단순 이동 평균 업데이트 + 2단계 독립 클리핑 |
| [[decisions/adr_004_amount_type]] | amount_type ENUM으로 불확정값 명시적 구분 |
| [[decisions/adr_005_llm_assist]] | Qwen2.5-Coder:14B 로컬 LLM, 함수 단위 제한 |
| [[decisions/adr_006_resnet]] | **Rule-based 폐기, 1D ResNet 채택** (2026-05-19) — MAPE 비교 불가가 핵심 사유 |
| ADR-007 (`decisions_summary` §ADR-007) | NN_v2+lookup 하이브리드 채택 (2026-05-20) — 합성타겟 한정 우위(실측 철회) |
| ADR-008 (`decisions_summary` §ADR-008) | **✅ 승인(2026-06-28) — 예측 엔진 포기 → 캘리브레이션 도구. NFR-01 확정 재정의(정확도 20% 폐기 → 비선형 존재 입증 + 수렴성)** |

---

## Analysis — 통계 분석 결과

| 페이지 | 한 줄 요약 |
|--------|-----------|
| [[analysis/mixedlm_b_results]] | b=0.6163, SE=0.1814, AIC=1262.87, ΔAIC=-67.13 |
| [[analysis/eda_summary]] | ratio∈[0.2,3.0] 확정, 950건 잔존, H0₃ p=0.655 기각 불가 |
| [[analysis/baseline_mape]] | 5-Fold CV b 표준편차 0.0275, 선형 대비 비교 기준 |
| [[analysis/feedback_b_validation]] | 피드백 보정 b≈0.74 최종 확정, MAPE 비교 생략 근거 (모집단 불일치) |
| [[analysis/realdata_engine_validation]] | **실측 6루프 검증 (2026-06-27)** — 실측 MAPE에서 선형≈최선, 모델·메트릭으론 해결 불가, Q3 캘리브레이션 재정의 |
| [[analysis/scaling_domains_survey]] | **유사 도메인 서베이 (6/27)** — 알로메트리(0.75)·6/10비용법칙(0.6)·Kleiber가 우리 식별성 진단 확증. "도메인 불가" 아닌 "데이터 인프라 결여" |

---

## Status — 구현 현황

| 페이지 | 한 줄 요약 |
|--------|-----------|
| [[status/module2_progress]] | 피드백 완료, 1D ResNet 엔진 구현 중 (5/19~) — Rule-based 폐기 |
| [[status/open_issues]] | git tag v-stat-complete, NN 구현, torch 의존성 추가 |

---

## Design — 코드 설계 결정

| 페이지 | 한 줄 요약 |
|--------|-----------|
| [[design/nn_resnet_design]] | **현행** 1D ResNet 엔진 설계 — (B,3,30) 입력, b_feedback 파생 Y 학습 |
| [[design/scaling_engine]] | (폐기) Rule-based 엔진 흐름 — 참조용 보존 |
| [[design/fallback_policy]] | 3단계 fallback 정책 — category_mean 최후 fallback 유지 |
| [[design/curvefit_policy]] | N≥10 셀만 실행, MixedLM 최우선, \|b_diff\|>0.1 처리 |
| [[design/calibration_direction]] | **캘리브레이션 선결과제 (6/27)** — (1+2)연구주장·검증프로토콜 / (3)데이터가능성·파일럿 / (4)스키마갭, 각 ≥3루프 |

---

## Reports & Narratives — 발표·논문용

| 페이지 | 한 줄 요약 |
|--------|-----------|
| `docs/for_reports/module2_transition_narrative.md` | **모듈 2 전환 narrative** — MAPE FAIL → CNN 전환 흐름, 발표 슬라이드 구조 제안 포함 |
| `docs/for_reports/baseline_mape_official.md` | 공식 Baseline MAPE 60.75% (n=454) |
| `docs/for_reports/h01_논문초안_v1.md` | H0₁ 논문 초안 |
| `docs/for_reports/h01_ttest_논문기재용.md` | H0₁ t-test 논문 기재용 |

---

## Sources — 문서 요약

| 페이지 | 한 줄 요약 |
|--------|-----------|
| (ingest 시 추가됨) | |

---

## Wiki 워크플로우 참조

- ingest/query/lint 규칙: `CLAUDE.md` §Wiki 워크플로우
- log: [[log]]
