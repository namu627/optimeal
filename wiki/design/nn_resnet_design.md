---
type: design
tags: [NN, ResNet, 스케일링엔진, PyTorch]
decision_date: 2026-05-19
status: 구현 완료 (정식 학습 대기)
implementation_date: 2026-05-19
---

# 1D ResNet 스케일링 엔진 설계

> **구현 상태 (2026-05-19)**: 4파일 작성 완료, Quick(10 epoch) 검증 통과 (mean MAPE 94.57%, 워밍업).
> **다음 단계**: `python module_2/src/engine/train_nn.py` 정식 학습 (60 epoch × 5-Fold).
> **상세 narrative**: [[../../docs/for_reports/module2_transition_narrative]] (발표 자료용)

## 결정 배경

Rule-based 엔진(lookup + fallback)을 폐기하고 1D ResNet으로 대체.

**전환 이유**:
- MAPE 등 지표로 Rule-based 정확도 입증 실패 (MixedLM b=0.6163 기반, 기존 데이터 오래됨)
- b_feedback(영양사 2인 이상 합의)이 현재 현장에 가장 근접한 참조 표준
- NN이 N을 연속 입력으로 받아 임의 인원수에 대한 일반화 가능

**학습 데이터 정의**:
- Y = base_amount × N^b_feedback → "측정 기반 파생 데이터"로 분류 (합성 데이터 아님)
- b_feedback 출처: scaling_coefficients.csv, estimation_method='nutritionist_feedback'

---

## 입력 텐서 설계

```
Shape: (B, 3, 30)

Channel 0: log(base_amount_g)     유효 슬롯만, 패딩은 0.0
Channel 1: category_id / 4.0     주재료=0.0, 부재료=0.25, 양념류=0.5, 수분류=0.75, 유지류=1.0
Channel 2: valid_mask             1=유효, 0=패딩
```

**슬롯 배정 (고정, 변경 금지)**:

| 슬롯 범위 | 카테고리 | 슬롯 수 |
|-----------|----------|---------|
| 0 ~ 5    | 주재료   | 6       |
| 6 ~ 11   | 부재료   | 6       |
| 12 ~ 23  | 양념류   | 12      |
| 24 ~ 27  | 수분류   | 4       |
| 28 ~ 29  | 유지류   | 2       |

카테고리 내 정렬: base_amount 내림차순. 슬롯 초과 재료는 제외(훈련 데이터 기준 최대 26~27종).

**N 입력**: log(N)을 CNN feature 추출 후 flatten과 concat.

---

## 출력

```
Shape: (B, 30)
값: log(scaled_amount_g) per slot
```

추론 시: `output = exp(model(x, N)) * valid_mask`
Loss 계산: valid_mask=1인 슬롯만.

---

## 아키텍처 (Shallow 1D ResNet)

```
Input (B, 3, 30)
  │
  ▼ Conv1d(3→32, k=3, pad=1) + BN + ReLU
  │
  ▼ ResBlock 1:
  │   Conv1d(32,32,k=3,p=1) + BN + ReLU
  │   Conv1d(32,32,k=3,p=1) + BN
  │   + skip(identity) → ReLU
  │
  ▼ ResBlock 2:
  │   Conv1d(32,64,k=3,p=1) + BN + ReLU
  │   Conv1d(64,64,k=3,p=1) + BN
  │   + skip(Conv1d(32,64,k=1)) → ReLU
  │
  ▼ Flatten → (B, 64×30=1920)
  │
  ▼ Concat log(N) → (B, 1921)
  │
  ▼ Linear(1921→128) + ReLU + Dropout(0.3)
  │
  ▼ Linear(128→30)
```

**Loss**: `MSELoss(pred[mask], target[mask])` — log 공간, 마스킹 슬롯만.
**Optimizer**: Adam, lr=1e-3, weight_decay=1e-4.
**Seed**: `torch.manual_seed(42)`.

---

## 학습 데이터 생성

**소스 파일**: `module_2/df_B.csv` (607행, pair_id/ingredient 단위)
**N 확장**: {10, 20, 50, 100, 150, 200, 300} — 7종

```python
# Y 생성 수식
b = get_b_feedback(group_type, derived_category)  # scaling_coefficients.csv 참조
Y = base_amount_g * (N ** b)
```

**분할**: GroupKFold(n_splits=5), 그룹 단위 = small_recipe_id (data leakage 방지).

---

## 평가 지표

| 지표 | 설명 | 목적 |
|------|------|------|
| MSE (log 공간) | 학습 loss | 수렴 모니터링 |
| MAPE (원 공간) | 실제 오차율 | 논문 보고용 |
| MAPE vs 선형 | 선형 대비 개선율 | FR-05 기준 (≥20% 개선) |

**논문 기술**: "b_feedback는 현업 영양사 합의 참조 표준. NN은 이 표준을 임의 레시피에 일반화 적용하는 모듈."

---

## 파일 구조 (구현 완료, 2026-05-19)

```
module_2/src/engine/
  ├── nn_training_data.py  ✅ 학습 데이터 생성 (df_B.csv → 텐서, 205레시피×7N=1435 샘플)
  ├── nn_scaling_engine.py ✅ ResNet1D 모델 + train/eval/save/load/predict
  ├── train_nn.py          ✅ 5-Fold CV 진입점 (--quick 옵션)
  ├── lookup.py            ✅ (유지) b_feedback 조회 참조용
  ├── fallback.py          ✅ (유지, 비활성) 비교 기준 보존
  ├── feedback_update.py   ✅ (유지) ADR-003 클리핑 수식
  ├── scaling_coefficients.csv  ✅ (확정 동결, 331행, 수정 금지)
  └── nn_model.pt          🔲 학습 후 자동 저장

module_2/tests/
  └── test_nn_engine.py    ✅ 20 PASS (정규화/텐서/순전파/마스킹/predict 계약/save&load)
```

---

## 학습 명령

```bash
source .venv/bin/activate
python module_2/src/engine/train_nn.py --quick                  # 10 epoch 검증
python module_2/src/engine/train_nn.py 2>&1 | tee module_2/src/engine/train_nn_run_20260519.log  # 정식 학습
```

---

## 의존성 (설치 완료)

```
torch==2.12.0        # 5/19 설치
scikit-learn         # 5/19 설치 (GroupKFold)
numpy, pandas        # 기존
```

> requirements.txt 반영은 담당자(권성민) 처리 예정 (CLAUDE.md "torch 신규, 5월 추가" 항목 근거).

## 관련 페이지
- [[design/scaling_engine]] — (폐기) Rule-based 엔진 이전 설계
- [[analysis/feedback_b_validation]] — b_feedback 값 및 논문 기술 방향
- [[decisions/adr_003_clipping]] — ADR-003 클리핑 수식 (feedback_update.py 유지)
