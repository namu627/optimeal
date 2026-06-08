---
type: decision
tags: [adr, mixedlm, 매칭, 3대분류]
source: docs/decisions_summary_v5_7.md
updated: 2026-04-06
---

# ADR-002 — 통계 전략 및 레시피 매칭

## 결정 (2026-02-22, v5: 2026-03-14)

**조리방법 3대분류 병합 → MixedLM 주전략 순차 적용**

### 조리방법 3대분류

| group_type | 해당 조리법 | 물리적 특성 |
|------------|------------|------------|
| `dry_heat` | 볶음, 구이, 부침 | 표면 수분 증발, 마이야르 반응 |
| `moist_heat` | 국·찌개, 찜, 조림, 삶기 | 국물 전체 수분 동태 |
| `no_heat` | 무침, 샐러드 | 수분 증발 없음 |

병합 근거: 통계적 편의가 아닌 **수분 동태 기반** → 논문 정당화 가능

### 통계 전략 단계

| 단계 | 내용 |
|------|------|
| Step 1 | 조리방법 6종 → 3대분류 병합 |
| Step 2 | EDA 후 셀당 n≥10 충족 시 독립 그룹 분리 |
| Step 3 | MixedLM 주 전략 |
| Step 4 | curve_fit은 N≥10 셀에 한해 보조·비교용 |

→ 세부 curve_fit 정책: [[design/curvefit_policy]]

## [v2] 적응형 그룹 전략 (2026-03-06)

**사전 선언 기준 (변경 금지)**:
- 셀당 n ≥ 10 → `SEPARATE` (독립 랜덤효과 그룹)
- 셀당 n < 10 → `MERGE` (3대분류 유지)

실제 결과 (2026-03-20): SEPARATE 18셀, MERGE 13셀

## [v3] 영양사 피드백 b값 (2026-03-07)

`estimation_method` 허용값:

| 값 | 도출 방법 | 역할 |
|----|----------|------|
| `mixedlm` | MixedLM | 방법론적 정당성 |
| `curve_fit` | scipy curve_fit | 시각화·비교용 |
| `category_mean` | 카테고리 평균 | fallback |
| `nutritionist_feedback` | 영양사 피드백 | 현장 적용 고도화 |

룩업 우선순위: `(cooking_method_id × category)` > `(group_type × category)` > `category_mean`

→ 상세: [[design/fallback_policy]]

## [v5] 가중 Jaccard 제거 (2026-03-14)

**복합점수 = 0.4 × 메뉴명유사도(SequenceMatcher) + 0.6 × 재료Cosine**

제거 근거: 소규모(평균 10~18종) vs 대규모(평균 5~13종) 이종 DB 기록 불균형으로 Jaccard 분모 체계적 과대평가 실증. 전수 스캔 583건 복합점수 최댓값 0.57 → 임계값 조정으로 해결 불가.

매칭 결과: 습열 82쌍 / 건열 59쌍 / 비가열 30쌍 = **171쌍 확정**
⚠ 비가열 30쌍은 구 기준(≥50쌍) 미달 → 논문 한계 절 명시 의무

## 교차 검증

- **GroupKFold (5-Fold)**, 분할 단위: `base_recipe_id`
- 일반 KFold 사용 금지 (data leakage 방지)

→ CV 결과: [[analysis/baseline_mape]]
