# 모듈 3 · CSP Solver (식단 자동 생성)

Google OR-Tools(CP-SAT) 기반 7일/31일 식단 생성. FR-11 / ADR-002 / ADR-008(CSP는 cold-start=선형과 병행).

## 파일 구성

| 파일 | 담당 | 내용 |
|------|------|------|
| `src/csp_solver.py` | pmy | CP-SAT 골조: 결정변수 `x(i,d,s)`, 끼니 구성(주식1·국1·반찬2), DB 메뉴 로더, 목적함수 훅 |
| `src/soft_constraints.py` | **ksm(권성민)** | **Soft 제약: 제공빈도·기호도·식단가** (본 문서 대상) |
| `tests/test_soft_constraints.py` | ksm | Soft 제약 검증 테스트 (14케이스) |

> Hard 제약(칼로리·알레르기·예산·영양 상한)과 나머지 Soft(색감·제철·메뉴 중복 회피)는 **별도 담당 태스크**이며 본 모듈 범위 밖.

## Soft 제약 설계 (제공빈도·기호도·식단가)

세 목적을 모두 **CP-SAT 정수-선형 점수식**으로 만들어, pmy 훅에서 `model.Maximize(score)` 로 합산한다.
"클수록 좋음" 부호 규약이라, 색감/제철/중복 담당이 자기 항을 `+` 로 더하기만 하면 조합된다.

### (1) 제공빈도 — 양방향 soft
- 규칙(`DEFAULT_FREQUENCY_RULES`): **잡곡밥·나물 주3회↑, 튀김·가공식품 주2회↓**.
- 유형 분류(`classify_food_types`): 실제 급식 메뉴명 근거 키워드 + 조달형태 `ingredient_type='COMMERCIAL'`(가공식품 권위 신호).
- 지평 스케일: `target = round(per_week × days/7)` (7일=그대로, 31일=13/9). 위반량 IntVar(`shortfall`/`excess`)를 페널티로.
- soft 이므로 후보가 없어도 **infeasible 되지 않고** 상수 페널티로 처리(우아한 저하).

### (2) 기호도
- `pref_scores[메뉴] = Σ(선호 +1 / 기피 −1)` — `constraints` 테이블(선호·기피)에서 도출(`load_preference_scores`).
- 데이터 없으면 **0(중립)** 으로 저하. 알레르기·절대금지는 Hard 영역이라 제외.

### (3) 식단가 최적화
- `cost_won`(pmy 로더가 `ingredient_price` 조인으로 계산)을 `cost_unit_won(=100원)` 단위 정수 계수로 하향 최적화.
- 예산 **상한(Hard)** 은 Hard 담당 몫. 여기서는 실행가능 범위 내 원가 절감만.

### 가중치 (`SoftWeights`, 튜너블)
`w_freq=10 ≫ w_pref=5 ≫ w_cost=1` — 빈도 최우선, 기호 반영, 원가 미세조정. `MealPlanRequest` 에 주입.

## 실행 / 테스트

```bash
# 테스트 (DB·네트워크 불필요, mock 메뉴)
pytest module_3/tests/test_soft_constraints.py -v

# 실제 식단 생성 (Postgres 가동 + 데이터 적재 필요)
python module_3/src/csp_solver.py --days 7
```

## 검증 결과 (3루프)
1. **동작**: 자동 테스트 14케이스 통과 — 분류기/항별 동작/엣지/통합.
2. **end-to-end**: `print_result` 로 7일 식단 생성 확인 — 빈도 목표 충족·기호 반영·원가 리포팅.
3. **적대적**: soft 충돌 시 우아한 저하(무크래시), 외부 Soft 항과 가산 합성 정상, flake8 클린.

## ⚠ 팀 확인 필요 사항
- **`ortools` 미포함**: `requirements.txt` 에 `ortools` 가 없다. 모듈 3 구동 필수 → **팀 회의로 추가 필요**(버전 변경 규칙).
  - 주의: 최신 `ortools 9.15` 는 메타상 `numpy>=2.0.2` 를 요구하나, 프로젝트 핀 `numpy 1.26.4`(scipy<2.3)와 **런타임은 호환**됨(cp_model 정상 import 확인). 버전 핀 시 검토 요망.
- **DB 미적재 항목**: `constraints`(선호/기피) 시드가 아직 없음 → 기호도는 현재 중립. 요청 시점 입력 또는 시드 필요.
- **범위 밖 미구현**: 메뉴 중복 회피(Soft)가 없어 데모 식단이 반복적임 — 해당 담당 구현 후 해소.
