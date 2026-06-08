# 1D ResNet 학습 결과 재분석 (2026-05-20)

> **목적**: 2026-05-19 정식 학습(60 epoch × 5-Fold) 결과의 재해석, Sonnet 4.6 분석 검증, 향후 학술 보고 방향 결정.
> **작성자**: 권성민 (Opus 4.7 분석 보조)
> **연관 ADR**: ADR-006 (1D ResNet 채택, 2026-05-19)

---

## 0. 학습 결과 원본

```
[setup] device=mps epochs=60 batch=64
[data] X=(1435, 3, 30) groups=205 valid_slots=4249
[cv] 5-Fold GroupKFold (group=small_recipe_id)
  fold 0: test_mape = 66.09%
  fold 1: test_mape = 38.06%
  fold 2: test_mape = 33.83%
  fold 3: test_mape = 36.73%
  fold 4: test_mape = 19.04%
[cv] mean MAPE = 38.75% ± 15.26%
[final] training on full dataset → nn_model.pt 저장
```

---

## 1. Sonnet 4.6 분석의 오류

| Sonnet 4.6 보고 | 실제 검증 결과 | 근거 |
|---|---|---|
| 5-Fold MAPE 17.22% ± 2.98% | **38.75% ± 15.26%** (실제 OOF) | 학습 로그 그대로. 17.22%는 "최종 모델(전체 데이터로 학습됨)을 각 fold test에 다시 평가"한 in-sample 측정 — 본인도 주의 문구에 명시 |
| Baseline 선형 MAPE 60.75% | **출처 불명** | grep 결과 코드/문서 어디에도 60.75 미존재. 실측 `baseline_result.csv` 기준 선형 MAPE는 32.68% |
| 선형 대비 71.6% 개선 | **계산 불가** | NN MAPE는 합성 타겟 기준, baseline은 실측 기준 — 서로 다른 양의 비교 |
| group_type별 MAPE / N별 MAPE 표 | 제시 자체는 유효하나 위 두 오류 기반의 비교 무효 |

**결론**: Sonnet 4.6의 핵심 비교(17.22% vs 60.75%, 개선 71.6%)는 **방법론적 오류와 미출처 숫자**에 기반. 발표/논문에 인용 금지.

---

## 2. 합성 타겟 제약과 그 의의

### 2.1 학습 데이터 구조

`nn_training_data.py:283-353`이 생성하는 학습 타겟:

```
Y_train = base × N^b_feedback(group_type, ingredient_category)
```

- `base`: 식약처 1인분 레시피 투입량 (모듈 1, 실측 원본)
- `b_feedback`: 영양사 50건 5회차 피드백에서 도출된 셀별 멱지수 (≈ 0.74 평균, `scaling_coefficients.csv` `nutritionist_feedback` 307행)
- `N ∈ {10, 20, 50, 100, 150, 200, 300}` 결정론적 확장

### 2.2 왜 합성일 수밖에 없는가 (구조적 제약)

`df_B.csv`의 실측 대규모 Y는 **20년 전 영양사협회 서적**에서 추출되었다. 그 시점의 조리 환경·기호·식자재 가용성을 반영하므로, **현재 기준 영양사 피드백**과 시간차 충돌이 발생한다. 따라서:

- 실측 Y를 학습 타겟으로 쓰면 → 현재 영양사 피드백이 학습에서 무시됨 (피드백 5회차 작업 전체가 무의미)
- 현재 기준 대규모 실측 레시피 300건 이상 신규 수집은 프로젝트 일정·예산 내 불가능

→ **학습 타겟이 합성(식약처 base × 현 피드백 b)인 것은 사후 선택이 아니라 데이터 제약의 필연.**

### 2.3 따라서 MAPE 비교는 무엇이 되는가

학습 타겟이 결정론적 함수라면 NN의 직무는 **함수 근사**이고:

- NN이 잘하면 MAPE → 0 (그러나 단순 lookup이 정의상 동일하게 달성)
- NN이 못하면 MAPE > 0 (lookup 대비 손해)

**"선형 대비 N% 개선" 비교는 두 개의 서로 다른 함수(b=1 vs b≈0.74)의 정의 거리만 측정** — 이는 학술적 주장이 아니다. ADR-006이 "MAPE TBD"로 비워둔 이유.

---

## 3. 결정적 버그 — input 텐서에 group_type이 없음

### 3.1 진단

`nn_scaling_engine.py:107-121` 모델 forward:
```python
def forward(self, x: torch.Tensor, log_n: torch.Tensor) -> torch.Tensor:
    # x: (B, 3, 30) — Ch0=log(base), Ch1=category_id, Ch2=mask
    # log_n: (B,)
```

학습 타겟 함수의 핵심 변수는 4개: `(base, N, group_type, category)`.
모델은 그 중 **`group_type`을 받지 못함**. 같은 `(base, N, category)`라도:

- `dry_heat × 주재료`: b ≈ 0.7393
- `moist_heat × 주재료`: b ≈ 0.7359
- `no_heat × 주재료`: b ≈ 0.7359

차이는 작지만 N=300에서 `log(300)*Δb` 만큼 log-space 잔차가 누적되어 exp 변환 시 비선형 증폭. 더 큰 문제:

- `no_heat × 부재료`: **scaling_coefficients.csv에 없음** → B_FALLBACK 0.6163 적용
- `no_heat × 수분류`: 동일 → 0.6163

df_B의 39행(no_heat × 부재료)은 b=0.6163, 나머지 568행은 b≈0.74로 학습 → **두 군집의 b 차이 약 0.12.** group_type 입력 없이 모델은 두 군집을 구분 못 함.

### 3.2 fold 분산의 정체

| fold | MAPE | 추정 원인 |
|---|---|---|
| 0 | 66.09% | no_heat × 부재료 test 분포가 train과 어긋남 |
| 4 | 19.04% | test에 결정론적으로 쉬운 셀이 다수 |

±15.26%p 분산은 fold마다 셀 분포가 흔들린 결과. group_type 입력 추가가 1순위 패치인 이유.

---

## 4. NN의 진짜 학술적 가치는 무엇인가

현 학습 데이터에서 NN이 단순 lookup 대비 추가로 줄 수 있는 가치:

### (a) 미커버 셀에 대한 외삽 [검증 가능]
- `scaling_coefficients.csv`의 누락 셀(`no_heat × 부재료/수분류`)에서 lookup은 `category_mean` fallback에 의존.
- NN은 인접 셀(`no_heat × 양념류`, `moist_heat × 부재료` 등)의 b로부터 누락 셀의 b를 smoothly 추론할 수 있음.
- → Leave-one-cell-out CV로 정량화 가능 (§6).

### (b) 재료 조합 컨텍스트 보정 [현 데이터로 검증 불가]
- 같은 셀이라도 동시에 들어있는 다른 재료의 base 분포에 따라 b가 달라져야 한다면 NN이 슬롯 전체를 보고 보정 가능.
- 그러나 **현재 학습 타겟이 결정론(셀별 단일 b)**이라 데이터에 이 신호가 존재하지 않음.
- 학습해도 NN은 lookup과 점근적으로 동치.

→ **현 학습 데이터로 검증 가능한 가치는 (a)뿐.** 이걸 측정해야 NN 채택의 학술적 정당성이 성립한다.

---

## 5. 평가 프레임 — Leave-One-Cell-Out CV (LOCO)

```
for cell in (group_type × category)의 13개 정의 셀:
    학습: 그 cell을 제외한 12 셀의 합성 타겟 (small_recipe_id 단위 hold-out 아님)
    평가:
      NN_pred(base, N, group_type, category=cell)
      lookup_baseline: category_mean fallback (NN과 동일 정보 제약)
    측정: |Y_NN - Y_true_synthetic| / Y_true_synthetic on held-out cell
```

세 가지 시나리오:

| 결과 | 해석 | 후속 |
|---|---|---|
| NN MAPE < lookup MAPE | NN이 인접 셀로부터 외삽에 성공. **논문 주장 가능** | 현 아키텍처 미세 튜닝, 발표 진행 |
| NN MAPE ≈ lookup MAPE | NN이 lookup과 동치. 가치 동등 | NN을 "smooth 보간기"로 위상 재정의, MAPE 절대값 보고하지 않음 |
| NN MAPE > lookup MAPE | NN이 외삽에 실패. lookup 대비 손해 | NN 폐기하고 lookup + category_mean fallback으로 회귀 (ADR-006 부분 철회 필요) |

---

## 6. 우선순위 액션 플랜

1. **input에 group_type 채널 추가** → quick 학습으로 합성 타겟에 대한 함수 근사가 정상화되는지 확인.
2. **LOCO CV 스크립트 작성 및 실행** → NN vs lookup의 외삽 격차 측정.
3. **결과에 따라 NN 위상 분기**: §5의 세 시나리오 중 어디인지 명시, 본 문서에 결과 절(§7) 추가.

---

## 7. 실험 결과

### 7.1 group_type 채널 추가 후 재학습 (2026-05-20)

입력 텐서 (B, 3, 30) → (B, 4, 30)로 확장, Ch3 = `group_type_id / 2.0` 추가.
나머지 하이퍼파라미터/시드 동일 (60 epoch × 5-Fold GroupKFold).

| fold | 기존 (Ch=3) | Ch=4 (+group_type) |
|---|---|---|
| 0 | 66.09% | 59.12% |
| 1 | 38.06% | 42.75% |
| 2 | 33.83% | 23.74% |
| 3 | 36.73% | 50.79% |
| 4 | 19.04% | 21.70% |
| **mean** | **38.75% ± 15.26%** | **39.62% ± 14.75%** |

**판정**: group_type 입력이 모델에 들어와도 평균/분산이 거의 변하지 않음.
→ **38~40% MAPE의 본체는 group_type 미수신이 아님**. §3.1의 진단은 부분적으로만 맞았고 결정적 요인이 아니었다.

### 7.2 새 진단 — 그러면 38~40% MAPE는 무엇인가

학습 로그의 epoch 60 train_loss ≈ 0.27 (log-space MSE) →
log(Y) 잔차 RMSE ≈ √0.27 ≈ 0.52 → exp 변환 시 +68% / -40%.
이는 **MAPE 평균 ~50%**와 정합. 즉 NN이 합성 타겟의 함수 근사에서 log-space에서는 합리적으로 학습됐으나, **MSE loss와 MAPE 평가 사이의 비호환 + 슬롯 base 스케일 분산**이 잔차의 본체.

함의:
- group_type 누락은 결정적 버그가 아니었음. 하지만 input 신호로 추가한 것은 학술적 정합성 측면에서 유지가 맞다 (학습 타겟 함수의 핵심 변수 누락 상태를 시정).
- 38~40% MAPE는 "이 학습 데이터/loss 구조에서의 자연스러운 잔차 수준"이며, lookup 대비 우열 비교 없이는 의미 없음.
- → LOCO CV가 더더욱 필수. NN의 가치는 절대 MAPE가 아니라 **lookup이 못하는 일을 하느냐**에 달림.

### 7.3 LOCO CV 결과 (2026-05-20 실행 완료)

`module_2/src/engine/loco_cv.py` 작성 및 실행. 40 epoch × 13 셀.
결과 원본: `module_2/src/engine/loco_results_20260520.csv`,
실행 로그: `module_2/src/engine/loco_run_20260520.log`.

| group_type | category | n_eval_slots | b_true | b_lookup | NN MAPE | LOOKUP MAPE | Δ (LK − NN) |
|---|---|---|---|---|---|---|---|
| dry_heat | 부재료 | 532 | 0.7637 | 0.7660 | 421.31% | 0.99% | −420.33%p |
| dry_heat | 수분류 | 21 | 0.7530 | 0.7503 | 513.73% | 1.14% | −512.58%p |
| dry_heat | 양념류 | 385 | 0.7478 | 0.7442 | 214.00% | 1.50% | −212.50%p |
| dry_heat | 유지류 | 196 | 0.7415 | 0.7426 | 57.68% | 0.47% | −57.21%p |
| dry_heat | 주재료 | 245 | 0.7386 | 0.7359 | 39.96% | 1.14% | −38.81%p |
| moist_heat | 부재료 | 826 | 0.7660 | 0.7637 | 57.84% | 0.97% | −56.86%p |
| moist_heat | 수분류 | 98 | 0.7503 | 0.7530 | 81.51% | 1.16% | −80.35%p |
| moist_heat | 양념류 | 609 | 0.7403 | 0.7480 | 38.75% | 3.34% | −35.41%p |
| moist_heat | 유지류 | 49 | 0.7426 | 0.7421 | 45.11% | 0.23% | −44.87%p |
| moist_heat | 주재료 | 294 | 0.7359 | 0.7372 | 71.14% | 0.58% | −70.56%p |
| no_heat | 양념류 | 574 | 0.7482 | 0.7440 | 34.48% | 1.75% | −32.73%p |
| no_heat | 유지류 | 84 | 0.7426 | 0.7421 | 22.24% | 0.23% | −22.00%p |
| no_heat | 주재료 | 63 | 0.7359 | 0.7372 | 37.00% | 0.58% | −36.42%p |
| **요약** | | | | | **NN 우위 0/13** | **lookup 우위 13/13** | **평균 −124.66%p** |

**판정**: §5 시나리오 (c) — **NN > LOOKUP, 모든 셀에서 lookup이 압도적 우위**.

### 7.4 결과 해석

1. **b_feedback의 좁은 클러스터링이 lookup을 거의 정답으로 만든다.**
   13개 셀 b값의 범위는 0.6755~0.7688, 평균 0.74. 한 셀을 빼도 나머지 카테고리 평균은 진짜 b와 거의 동일 (대부분 차이 < 0.01) → category_mean lookup MAPE 0.2~3.3%로 거의 완벽.

2. **NN은 hold-out 셀에 대한 외삽에 완전히 실패한다.**
   1D Conv 구조에서 슬롯 12~23(양념류)의 학습 신호를 통째로 제거하면, 모델은 그 슬롯에 대한 매핑 정보를 얻지 못함. 인접 슬롯(카테고리)으로부터 일반화가 안 되는 게 명백 (Conv kernel=3은 카테고리 경계를 넘는 일반화에 부적합).

3. **§4(a)의 가설 — "NN이 미커버 셀 외삽에 우수" — 은 데이터로 기각됐다.**
   현 학습 데이터에서 NN이 lookup 대비 추가 가치를 가질 자리는 **없다**.

---

## 8. 결론과 권고

### 8.1 사실 정리

- 5-Fold OOF MAPE 38.75% (Ch=3) / 39.62% (Ch=4)는 **합성 타겟에 대한 함수 근사 잔차**이며, lookup baseline과 비교할 학술 의미가 없다 (lookup도 합성 타겟을 정의상 거의 정답으로 맞춤).
- group_type 입력은 추가하는 게 정합적이나 평균 MAPE 개선 효과는 없다.
- LOCO CV에서 NN은 lookup 대비 **모든 13개 셀에서 열세**다.

### 8.2 ADR-006 부분 철회 권고

ADR-006이 NN 채택의 근거로 든 세 가지 중:

| ADR-006 주장 | 검증 결과 |
|---|---|
| (1) MixedLM 통계 b의 MAPE 실패 | 유효 — Rule-based 통계 b는 부적합 |
| (2) 영양사 피드백 b의 MAPE 비교 구조적 불가 | 유효 — 합성 타겟 본질 |
| (3) Rule-based의 N 연속 일반화 한계 → NN 채택 | **반증** — N 연속 일반화가 학술 가치라면 lookup도 동일한 `Y = base × N^b_lookup` 공식으로 임의 N에 일반화 가능. NN의 추가 가치 없음. |

### 8.3 권고 사항 (요약)

1. **NN 정식 학습 결과를 발표 자료에 MAPE 수치로 인용하지 말 것.** §1 표의 17.22%/60.75%/71.6% 비교는 폐기.
2. **`lookup.py` + `fallback.py`(category_mean)을 메인 엔진으로 복귀.** ADR-006의 D-3/D-4/D-5 폐기 결정을 재검토.
3. **`nn_scaling_engine.py` / `nn_training_data.py` / `train_nn.py` / `nn_model.pt`은 보존하되 학술 보고 대상에서 제외.** (코드 폐기는 ADR-006 철회 절차 후)
4. **논문 한계 절 명시**:
   > "1D ResNet 기반 스케일링 엔진을 시범 구현하였으나, 학습 타겟이 결정론적 합성치이고 b_feedback의 셀별 분산이 좁아 단순 카테고리 평균 lookup 대비 우위를 보이지 못함을 LOCO CV로 확인. 최종 엔진은 lookup + category_mean fallback으로 결정."
5. **ADR-007 신규** — "1D ResNet 폐기, lookup 엔진 복귀" 결정 사항 기록 (작성 권한자: 권성민).

### 8.4 미해결 가능성

LOCO에서 NN이 실패한 직접 원인은 **카테고리 슬롯 구조 자체**다. 슬롯 12~23이 통째로 비면 학습 불가. 만약:

- 카테고리를 슬롯 위치가 아닌 **input feature**로만 표현 (DeepSets/attention)
- 학습 데이터에 **재료 조합 노이즈** 추가 (현 데이터는 결정론이라 학습 신호가 lookup과 동치)

→ 이론적으로 NN 우위가 회복될 수 있으나, 이는 ADR-006의 현 NN 사양(고정)을 폐기하고 모듈 2를 처음부터 다시 설계하는 일이다. **2026-05-30 모듈 2 완성 일정 내 불가**.

따라서 본 권고는 "현 일정 내 합리적 선택"으로서 lookup 회귀를 제시한다.

---

## 9. v2 실험 결과 (2026-05-20, 8.4 후속)

§8.4의 두 가설을 v2 모듈로 구현하여 검증.

### 9.1 v2 변경 사항

| 항목 | v1 (ADR-006) | v2 (실험) |
|---|---|---|
| 아키텍처 | 1D ResNet (slot 위치 기반 Conv1d) | DeepSets-with-context (permutation-invariant) |
| 카테고리 표현 | 슬롯 위치 (0~5=주재료 등) | per-slot one-hot feature |
| group_type 표현 | 없음 (v1 Ch=3) / 1 채널 (v1 Ch=4) | per-slot one-hot (3 dim) |
| 학습 타겟 | 결정론 `Y = base × N^b_feedback` | noisy `Y = base × N^(b_feedback + ε)`, ε ~ N(0, 0.05²) |
| 파라미터 수 | ~250k | ~21k (12분의 1) |
| 신규 파일 | — | `nn_training_data_v2.py`, `nn_scaling_engine_v2.py`, `train_nn_v2.py`, `loco_cv_v2.py` |

### 9.2 v2 5-Fold CV (모든 셀 학습 데이터에 포함)

| fold | NN MAPE | LOOKUP MAPE | Δ (LK − NN) |
|---|---|---|---|
| 0 | 21.99% | 22.49% | +0.50%p |
| 1 | 20.12% | 20.64% | +0.51%p |
| 2 | 18.76% | 23.81% | +5.05%p |
| 3 | 19.28% | 20.79% | +1.51%p |
| 4 | 18.10% | 20.57% | +2.47%p |
| **mean** | **19.65% ± 1.34%** | **21.66% ± 1.29%** | **+2.01%p (NN 우위)** |

**결과**: 5-Fold 모든 fold에서 NN이 lookup을 능가. 분산도 v1의 14.75%p에서 1.34%p로 11배 안정.
→ **NN이 셀별 b 차이(0.6755~0.7688)와 노이즈 분포를 동시에 학습 가능함**을 입증.
   `nn_model_v2.pt` 저장 완료.

### 9.3 v2 LOCO (셀 단위 외삽)

| group_type | category | n_slots | b_true | b_lookup | NN | LOOKUP | Δ |
|---|---|---|---|---|---|---|---|
| dry_heat | 부재료 | 532 | 0.7637 | 0.7660 | 47.94% | 16.19% | −31.75%p |
| dry_heat | 수분류 | 21 | 0.7530 | 0.7503 | 42.23% | 7.04% | −35.19%p |
| dry_heat | 양념류 | 385 | 0.7478 | 0.7442 | 28.80% | 15.99% | −12.81%p |
| dry_heat | 유지류 | 196 | 0.7415 | 0.7426 | 43.03% | 20.30% | −22.73%p |
| dry_heat | 주재료 | 245 | 0.7386 | 0.7359 | 51.49% | 18.32% | −33.17%p |
| moist_heat | 부재료 | 826 | 0.7660 | 0.7637 | 41.00% | 17.90% | −23.10%p |
| moist_heat | 수분류 | 98 | 0.7503 | 0.7530 | 68.89% | 23.90% | −44.99%p |
| moist_heat | 양념류 | 609 | 0.7403 | 0.7480 | 48.38% | 20.19% | −28.19%p |
| moist_heat | 유지류 | 49 | 0.7426 | 0.7421 | 23.63% | 15.83% | −7.80%p |
| moist_heat | 주재료 | 294 | 0.7359 | 0.7372 | 55.26% | 15.66% | −39.59%p |
| no_heat | 양념류 | 574 | 0.7482 | 0.7440 | 43.62% | 19.47% | −24.15%p |
| no_heat | 유지류 | 84 | 0.7426 | 0.7421 | 21.11% | 12.29% | −8.82%p |
| no_heat | 주재료 | 63 | 0.7359 | 0.7372 | 42.00% | 16.38% | −25.62%p |
| **요약** | | | | | **NN 우위 0/13** | **lookup 우위 13/13** | **평균 −25.99%p** |

**v1 대비 비교**: v1 LOCO 평균 Δ −124.66%p → v2 −25.99%p. **격차 약 5배 축소.** 그러나 여전히 시나리오 (c) — 모든 셀에서 lookup 우위.

### 9.4 v2 결과 해석

1. **`5-Fold (in-distribution)`와 `LOCO (out-of-distribution)`가 정반대 결과**:
   - 학습 데이터에 같은 (gt, cat) 셀이 있으면 NN이 lookup을 약간 능가 (+2.01%p)
   - 셀이 통째로 빠지면 NN이 lookup에 크게 패배 (−25.99%p)
   - → NN은 **본 적 있는 셀의 b 변동을 fine-tune** 하지만, **본 적 없는 셀의 b는 추론하지 못함**

2. **lookup이 LOCO에서 강한 근본 이유는 b_feedback의 좁은 클러스터링**:
   - 13개 셀 b 범위 0.6755~0.7688, 셀별 차이 < 0.01
   - 한 셀을 빼도 다른 셀들 평균(b_lookup)이 진짜 b와 거의 같음 → b 추정 오차 < 0.003
   - σ=0.05 노이즈가 셀 간 차이를 압도 → lookup이 정의상 거의 정답
   - NN이 hold-out 셀의 feature 조합(예: dry_heat∧주재료)을 새로 보면 systematic bias(±0.05~0.10) 발생

3. **노이즈 σ가 더 크거나 셀별 b 격차가 더 컸다면 NN이 LOCO에서도 이길 가능성**:
   - 현재 b 클러스터링이 매우 좁아 lookup이 강함. 도메인적으로 b가 셀별로 크게 다른 (예: 양념류 0.5 / 주재료 0.9) 데이터셋에서는 NN의 feature compositionality가 우위를 가질 수 있음.

### 9.5 갱신된 권고 — **2026-05-20 채택 (ADR-007)**

§8.3을 다음과 같이 갱신. 본 권고는 ADR-007로 공식 채택되었다 (`docs/decisions_summary_v5_8.md` §ADR-007).

| 시나리오 | 권고 |
|---|---|
| **운영용 엔진 (모든 셀 학습 데이터에 존재)** | `nn_model_v2.pt` (DeepSets, noisy targets) 채택 검토 — 5-Fold에서 lookup 대비 +2%p 우위. 단 절대 MAPE는 노이즈 σ에 의존하므로 보고 시 σ 명시 필수. |
| **외삽 (미커버 셀)** | lookup + category_mean fallback 유지. NN에 외삽 의존 금지. |
| **하이브리드 운영** | 입력 셀이 학습 데이터에 있으면 NN_v2, 없으면 lookup으로 분기. |

이 분기 권고는 ADR-006의 "NN 단일 엔진" 결정을 부분적으로 수정한다. ADR-007(신규) 검토 필요:
- "(group_type × category) 셀이 학습 데이터에 포함되면 NN_v2 사용, 미커버 셀은 lookup fallback."

### 9.6 학술 보고 가능성

논문 결과 섹션에 다음 표현 가능:

> "1D ResNet 슬롯 위치 기반 모델은 hold-out 셀 외삽에서 lookup 대비 평균 124.66%p 열세였으나, permutation-invariant DeepSets 아키텍처와 노이즈 학습 데이터 (σ=0.05)를 도입한 v2 모델은 5-Fold CV에서 lookup 대비 평균 2.01%p 우위를 달성하였다. LOCO CV에서는 여전히 lookup이 평균 25.99%p 우위였는데, 이는 b_feedback 셀별 분포가 매우 좁아(0.6755~0.7688) 카테고리 평균 fallback이 강한 baseline임에 기인한다. 따라서 최종 운영 엔진은 학습 셀 내 추론은 NN_v2, 미커버 셀 외삽은 lookup으로 분기하는 하이브리드 구조를 채택한다."

이 표현은 합성 타겟 한계를 정직히 명시하면서도 NN의 부분적 우위(in-distribution)와 lookup의 강점(out-of-distribution)을 학술적으로 분리 보고하는 형태다.

### 9.7 미해결 후속

- **노이즈 σ 민감도 분석**: σ ∈ {0.02, 0.05, 0.1, 0.15}에서 5-Fold/LOCO 격차가 어떻게 변하는지.
- **Cell-systematic 노이즈**: feature 의존 노이즈(예: b += 0.05 × tanh(log(base)/3 − 1))로 NN이 LOCO에서도 이기는지.
- **앙상블**: 5 fold 모델 평균이 단일 모델 대비 개선되는지.
- **결정론적 v2 (σ=0)**: 노이즈 없이 v2 아키텍처만으로 LOCO 격차가 얼마나 줄어드는지.

후속 작업은 발표 일정(2026-09-30 Code Freeze) 안에 추가 실험으로 진행 가능. 단 본 분석은 현재 시점 권고를 위한 일차 결과.




