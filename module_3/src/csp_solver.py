# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · CSP 모델 정의 코드 (Google OR-Tools CP-SAT) — 기본 골조
7일/31일 식단 생성 문제를 OR-Tools로 정의하는 기본 골격.
- 결정변수 : x(i,d,s) ∈ {0,1}  (메뉴 i를 d일 s끼니에 편성)
- 모델 구조: 끼니 슬롯 구성 (밥 1·국 1·반찬 2)
- 목적함수 : (Soft 제약 담당자가 구현) ★build_and_solve의 목적함수 구역에 추가★
- 데이터   : 실 DB(nutrition_recipe 등) 조회
구성: (1) 데이터 계층  (2) 입력/결과 자료구조  (3) 모델 구성  (4) 출력  (5) CLI
기준 문서: 제약조건 정의서 / COP 사전설계서 / FR-11 / ADR-008
※ Hard 제약(칼로리·알레르기·예산·영양기준 등)은 'Hard Constraint 구현' 태스크에서 추가.
"""
from __future__ import annotations
import argparse
import os
import time
from dataclasses import dataclass, field
from datetime import date
from ortools.sat.python import cp_model
# 제약 모듈 (같은 패키지). 스크립트/모듈 양쪽 실행 지원.
# 모두 동일 규약(모델 주입·키워드 인자·결과 dataclass·evaluate 동반)의 순수 함수 모듈이며,
# 뼈대(build_and_solve)는 이들을 같은 호출식으로 부른다.
#   hc  = pmy(박미연): Hard — 알레르기·칼로리·예산 (제약)
#   sc  = ksm(권성민): Soft — 제공빈도·기호도·식단가 (목적함수)
#   scd = nyc(남유찬): Soft — 다양성·제철·완제품자제·나트륨당저감 (목적함수)
#   ws  = 롤링 웜스타트 (탐색 보조 — 해의 의미를 바꾸지 않음)
try:
    from . import csp_hard_constraints as hc
    from . import csp_warm_start as ws
    from . import menu_affinity as ma
    from . import menu_taxonomy as mt
    from . import soft_constraints as sc
    from . import soft_constraints_diversity as scd
    from . import user_profiles as up
except ImportError:  # `python csp_solver.py` 직접 실행 시
    import csp_hard_constraints as hc
    import csp_warm_start as ws
    import menu_affinity as ma
    import menu_taxonomy as mt
    import soft_constraints as sc
    import soft_constraints_diversity as scd
    import user_profiles as up
# ===========================================================================
# (1) 데이터 계층 — 메뉴 후보 공급 (실 DB)
# ===========================================================================
@dataclass
class MenuItem:
    """결정변수 x(i,d,s)의 대상이 되는 메뉴 후보 하나."""
    menu_id: int                       # nutrition_recipe.nutrition_id
    name: str                          # nutrition_recipe.recipe_name
    category: str                      # nutrition_recipe.menu_category
    calories: float                    # nutrition_recipe.calories (kcal)
    cost_won: float = 0.0              # 1인분 식재료비 (원)
    colors: set = field(default_factory=set)     # 색감 집합 ← 색감 목적함수
    allergens: set = field(default_factory=set)  # 알레르겐 재료명 집합 (Hard 태스크에서 사용)
    season_score: float = 0.0          # 제철 빈도 점수 (0~1) ← 제철 목적함수
def get_engine():
    """DB 접속 엔진(PostgreSQL). 접속 정보는 환경변수(POSTGRES_*)에서 읽는다.
    - 컨테이너 실행(docker exec): compose가 POSTGRES_HOST=db 주입 → 'db'.
    - 호스트(venv) 실행: 환경변수 없음 → 기본 'localhost'(도커가 5432 게시).
    """
    from sqlalchemy import create_engine
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "optimeal")
    user = os.getenv("POSTGRES_USER", "optimeal")
    password = os.getenv("POSTGRES_PASSWORD", "optimeal_dev_pw")
    return create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}")
# 식단에 실제 배치되는 카테고리만 조회(식약처 30만 '기타' 제외 → 결정변수 폭발 방지).
#   2026-08-12: '반찬' 한 덩어리 → 주찬/부찬/김치로 세분(영양사 지적). 아직 재분류하지
#   않은 DB 를 위해 '반찬'도 조회 목록에 남겨 두고, 세분 후보가 없으면 경고한다.
DEFAULT_MENU_CATEGORIES = ["주식", "국", "찌개", "주찬", "부찬", "김치", "반찬"]
# 반상 끼니 구성 — 영양사 확정(2026-08-12): 주식1·국1·주찬1·부찬 2~3·김치1.
#   값이 int 면 정확히 그 개수, (lo, hi) 튜플이면 범위(hi=None 이면 상한 없음).
#   부찬 상한 3은 현장 배식 기준(영양사 확정) — 상한이 없으면 열량 밴드가 허용하는 만큼
#   4개까지 늘어난다(8/12 1차 산출물에서 63끼니 중 6끼니가 4개였다).
DEFAULT_COMPOSITION = {"주식": 1, "국": 1, "주찬": 1, "부찬": (2, 3), "김치": 1}
_MENU_QUERY = """
WITH latest_price AS (
    SELECT DISTINCT ON (ingredient_id) ingredient_id, price_per_g
    FROM ingredient_price ORDER BY ingredient_id, price_date DESC
),
allergen AS (
    SELECT DISTINCT ingredient_id FROM constraints WHERE constraint_type = '알레르기'
)
SELECT
    nr.nutrition_id AS menu_id, nr.recipe_name AS name, nr.menu_category AS category,
    nr.calories AS calories,
    COALESCE(SUM(lp.price_per_g * rim.per_serving_grams), 0) AS cost_won,
    COALESCE(AVG(si.freq_score), 0) AS season_score,
    ARRAY_REMOVE(ARRAY_AGG(DISTINCT ing.color_category), NULL) AS colors,
    ARRAY_REMOVE(ARRAY_AGG(DISTINCT CASE WHEN al.ingredient_id IS NOT NULL
                                         THEN ing.ingredient_name END), NULL) AS allergens
FROM nutrition_recipe nr
LEFT JOIN recipe r               ON r.nutrition_recipe_id = nr.nutrition_id
LEFT JOIN recipe_ingredient_map rim ON rim.recipe_id = r.recipe_id
LEFT JOIN ingredient ing         ON ing.ingredient_id = rim.ingredient_id
LEFT JOIN latest_price lp        ON lp.ingredient_id = ing.ingredient_id
LEFT JOIN allergen al            ON al.ingredient_id = ing.ingredient_id
LEFT JOIN seasonal_ingredient si ON si.ingredient_id = ing.ingredient_id AND si.month = :month
WHERE nr.menu_category = ANY(:categories)
GROUP BY nr.nutrition_id, nr.recipe_name, nr.menu_category, nr.calories
"""
def load_menus(month: int | None = None,
               categories: list[str] | None = None) -> list[MenuItem]:
    """실 DB(nutrition_recipe 등)에서 메뉴 후보를 조회한다."""
    from sqlalchemy import text
    month = month or date.today().month
    categories = categories or DEFAULT_MENU_CATEGORIES
    menus: list[MenuItem] = []
    with get_engine().connect() as conn:
        rows = conn.execute(text(_MENU_QUERY),
                            {"month": month, "categories": categories}).mappings()
        for r in rows:
            menus.append(MenuItem(
                menu_id=r["menu_id"], name=r["name"], category=r["category"],
                calories=float(r["calories"] or 0), cost_won=float(r["cost_won"] or 0),
                colors=set(r["colors"] or []), allergens=set(r["allergens"] or []),
                season_score=float(r["season_score"] or 0),
            ))
    if not menus:
        raise RuntimeError("DB에서 메뉴 후보를 찾지 못했습니다 — 데이터 적재 상태를 확인하세요.")
    _warn_if_sides_unclassified(menus)
    return menus


def _warn_if_sides_unclassified(menus: list[MenuItem]) -> None:
    """'반찬'만 있고 주찬/부찬/김치가 없으면 재분류 미실행이므로 알려 준다.

    이 상태로 기본 구성을 풀면 원인 없이 INFEASIBLE 이 나므로, 조용히 실패하지 않게
    한다(2026-08-12 반상 세분 도입).
    """
    cats = {m.category for m in menus}
    if "반찬" in cats and not (cats & set(mt.SIDE_KINDS)):
        print("[경고] menu_category 에 주찬/부찬/김치가 없습니다 — "
              "`python scripts/reclassify_menu_categories.py --apply` 로 재분류하세요.")
# ===========================================================================
# (2) 입력 / 결과 자료구조 (FR-11)
# ===========================================================================
@dataclass
class MealPlanRequest:
    days: int = 7                                   # 급식 일수 (7/31)
    meals: tuple = ("아침", "점심", "저녁")          # 끼니
    # 끼니 슬롯 구성. int=정확히 그 개수 / (lo, hi)=범위(hi=None 이면 상한 없음).
    composition: dict = field(default_factory=lambda: dict(DEFAULT_COMPOSITION))
    solver_time_limit: float = 10.0
    # 롤링 웜스타트(초기해 hint) 사용 여부. 기본 ON.
    #   31일 풀이의 병목은 "첫 가능해 찾기"이며(나트륨 일 상한이 558슬롯을 전역 결합),
    #   순차 구성한 초기해를 넣으면 24.8~65.5초(한도 초과 발생) → 10.1~10.8초로 고정된다.
    #   힌트는 탐색 출발점일 뿐이라 **가능영역·최적성의 의미를 바꾸지 않는다**(틀린 힌트는 폐기됨).
    #   끄고 싶을 때(예: 힌트 효과 비교 측정) False. 상세: `csp_warm_start` 모듈 docstring.
    warm_start: bool = True
    # ── Hard 제약(②알레르기·③칼로리·④예산) ─────────────────────────────
    #   opt-in: None이면 미적용(Soft 로직만). 운영/CLI는 HardConstraintConfig를
    #   주입해 켠다. 기준값(2000kcal·3500원 등)은 운영데이터로 확정 예정(명세서 §6).
    #   대체식 분기 태스크에서 hard.excluded_allergens를 채우면 ②가 활성화된다.
    hard: object = None                # hc.HardConstraintConfig (None=미적용)
    # Hard 영양소 제약(H-2d 최소·H-2e 상한)이 읽을 값 {영양소명: {메뉴인덱스: 양}}.
    #   MenuItem 에 없는 영양소(나트륨 등)를 side-channel 로 넘기는 통로.
    #   예: sodium_by_idx, _ = scd.load_nutrition_fields(engine, menus)
    #       hard_nutrient_by_idx={"sodium": sodium_by_idx}
    hard_nutrient_by_idx: dict = None
    # ── Soft 목적함수 파라미터 ───────────────────────────────────────────
    #   ksm(제공빈도·기호도·식단가) + nyc(다양성·제철·나트륨당) 를 합산 Maximize.
    soft_weights: object = None        # sc.SoftWeights (None=기본값)
    pref_scores: dict = None           # {메뉴 인덱스: 기호도 점수} (None=중립)
    group_id: int = None               # 기호도 DB 조회용 타겟 그룹(선택)
    # 식단가 하한(원). 하루 총원가가 이 값 미만이면 Soft 감점(품질·만족도 프록시).
    #   None=하한 미적용(현행 동작 불변). 켤 때는 가격 커버리지 점검 후 사용.
    budget_floor_won: float = None
    diversity_weights: object = None   # scd.DiversityWeights (None=기본값)
    # 주재료 축 {메뉴 인덱스: 대표 주재료명} — scd 의 주재료 중복 회피 항이 읽는다.
    #   MenuItem 에 없는 값이라 side-channel 로 넘긴다(B6 Phase 1, 2026-08-14).
    #   예: main_by_idx=scd.load_main_ingredients(get_engine(), menus)
    #   None/빈 dict 면 항이 자동 비활성(우아한 저하) → 8/13 이전과 동일 동작.
    main_by_idx: dict = None
    # 메뉴 어울림 근거표 [ma.AffinityRow] — 어울림 Soft 항이 읽는다(B6 Phase 2, 2026-08-15).
    #   점수를 코드가 아닌 데이터로 두는 구조라 주입 경로도 side-channel 이다.
    #   예: affinity_table=ma.load_affinity_table()
    #   None/빈 목록이면 항이 자동 비활성 → 8/14 이전과 동일 동작.
    affinity_table: list = None
    affinity_weights: object = None    # ma.AffinityWeights (None=기본값)
@dataclass
class MealPlanResult:
    status: str
    objective: float
    wall_time: float     # 총 소요(웜스타트 구성 + 솔버 풀이) — SLA 는 이 값으로 잰다
    plan: dict           # {day: {meal: [menu_name, ...]}}
    daily_kcal: dict     # {day: 총kcal} (참고용)
    total_cost: int      # 1인 총 식재료비 (참고용)
    warm_start_seconds: float = 0.0    # 그중 초기해 구성에 쓴 시간(0=웜스타트 미사용)
    hard_breakdown: dict = None        # pmy Hard 지표(칼로리·예산·알레르기 준수) — 미적용/미풀이 시 None
    soft_breakdown: dict = None        # ksm Soft 지표(제공빈도·기호도·원가)
    diversity_breakdown: dict = None   # nyc Soft 지표(다양성·제철·나트륨당)
    affinity_breakdown: dict = None    # 어울림 지표(조리법 중복·주식×국) — 미주입 시 None
# ===========================================================================
# (3) 모델 구성 — 결정변수 + 끼니 구성 + 목적함수
# ===========================================================================
def _count_bounds(spec) -> tuple:
    """구성 값(int 또는 (lo, hi))을 (하한, 상한)으로 정규화한다.

    Args:
        spec: 정확 개수(int) 또는 (최소, 최대) 튜플. 최대가 None 이면 상한 없음.

    Returns:
        (lo, hi). 각각 None 이면 해당 방향 제약 없음.
    """
    if isinstance(spec, (tuple, list)):
        lo, hi = (list(spec) + [None, None])[:2]
        return lo, hi
    return spec, spec


def build_and_solve(menus: list[MenuItem], req: MealPlanRequest) -> MealPlanResult:
    model = cp_model.CpModel()
    D = range(req.days)
    S = range(len(req.meals))
    M = range(len(menus))
    # 결정변수 x(i,d,s): 메뉴 i를 d일 s끼니에 편성하면 1
    x = {(m, d, s): model.NewBoolVar(f"x_{m}_{d}_{s}") for m in M for d in D for s in S}
    # ---------------------- 모델 구조: 끼니 슬롯 구성 ----------------------
    # 각 끼니 = 주식1·국1·주찬1·부찬 2이상·김치1(기본). 구성 외 카테고리(후식·음료 등)는 배제.
    for d in D:
        for s in S:
            for cat, spec in req.composition.items():
                lo, hi = _count_bounds(spec)
                picked = sum(x[m, d, s] for m in M if menus[m].category == cat)
                if lo is not None:
                    model.Add(picked >= lo)
                if hi is not None:
                    model.Add(picked <= hi)
            for m in M:
                if menus[m].category not in req.composition:
                    model.Add(x[m, d, s] == 0)
    # ---------------------- Hard 제약 (②③④) — pmy, opt-in --------------
    # Soft 와 동형 호출식. req.hard(config)가 주어졌을 때만 결합한다.
    #   excluded_allergens=∅ 이면 ②알레르기는 비활성(일반식), ③칼로리·④예산만 적용.
    #   excluded_allergens 를 채우면 ②까지 활성(대체식 분기에서 사용).
    hard = None
    if req.hard is not None:
        hard = hc.add_hard_constraints(
            model, x, menus,
            days=req.days, n_meals=len(req.meals),
            config=req.hard,
            nutrient_by_idx=req.hard_nutrient_by_idx,
        )
    # ---------------------- 목적함수 (Soft) — ksm + nyc 합산 Maximize ------
    # 두 Soft 모듈은 동일 규약("클수록 좋음")·동일 x 키를 쓰므로 점수식을 더한다.
    #   sc  : 제공빈도·기호도·식단가            (권성민)
    #   scd : 다양성·제철·완제품자제·나트륨당    (남유찬)
    soft = sc.add_soft_objective(
        model, x, menus,
        days=req.days, n_meals=len(req.meals),
        weights=req.soft_weights,
        pref_scores=req.pref_scores,
        budget_floor_won=req.budget_floor_won,
    )
    div = scd.add_diversity_soft_objective(
        model, x, menus,
        days=req.days, n_meals=len(req.meals),
        weights=req.diversity_weights,
        main_by_idx=req.main_by_idx,
    )
    # 어울림(B6 Phase 2) — 근거표를 주입했을 때만 활성. 전 항이 **끼니 국소**라
    # 웜스타트 하루 부분 문제에도 같은 함수를 그대로 걸 수 있다(아래 참조).
    aff = ma.add_affinity_soft_objective(
        model, x, menus,
        days=req.days, n_meals=len(req.meals),
        table=req.affinity_table,
        weights=req.affinity_weights,
    )
    model.Maximize(soft.score + div.score + aff.score)
    # ---------------------- 롤링 웜스타트 (탐색 보조) ----------------------
    # 하루씩 순차로 구성한 배치를 초기해로 넣는다. 모델 자체는 그대로 풀리므로
    # 해의 의미(가능영역·최적성)는 불변 — 힌트가 틀리면 솔버가 버릴 뿐이다.
    # 힌트 구성에 쓴 시간만큼 풀이 예산에서 뺀다(총 소요가 SLA 를 넘지 않게).
    # req.hard 가 None 이면 Hard 제약 자체가 없어 첫 해를 바로 찾으므로 힌트가 무의미하다.
    #   그때 기본 config 로 힌트를 만들면 모델에 없는 제약(2000kcal·예산)을 혼자 지키려다
    #   시간만 버린다 → 같은 config 가 있을 때만 켠다.
    hint_seconds = 0.0
    if req.warm_start and req.hard is not None:
        t_hint = time.monotonic()
        # 주재료 cap 은 지평 규칙이라 힌트도 같은 cap 을 알아야 감점 0에 닿는다.
        dw = req.diversity_weights or scd.DiversityWeights()
        hint = ws.build_rolling_hint(
            menus, days=req.days, n_meals=len(req.meals),
            composition={c: _count_bounds(v) for c, v in req.composition.items()},
            config=req.hard, nutrient_by_idx=req.hard_nutrient_by_idx,
            main_by_idx=req.main_by_idx,
            main_cap=scd.scale_targets(dw.main_cap_per_week, req.days),
            affinity_table=req.affinity_table,
            affinity_weights=req.affinity_weights,
            time_budget=req.solver_time_limit * ws.DEFAULT_BUDGET_RATIO)
        ws.apply_hint(model, x, hint, days=req.days, n_meals=len(req.meals))
        hint_seconds = time.monotonic() - t_hint
    # ---------------------- 풀이 및 결과 추출 -----------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(1.0, req.solver_time_limit - hint_seconds)
    status = solver.Solve(model)
    plan, daily_kcal, total_cost = {}, {}, 0
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # ⚠ 접시별 int() 절단 금지: Hard 제약은 int(kcal*SCALE)(소수 2자리)로 걸리는데
        #   리포트가 접시마다 int()로 버리면 하루 12접시에서 최대 ~12kcal 과소 집계되어
        #   제약을 만족한 식단이 "칼로리 위반"으로 표시된다. 합산을 float로 하고 마지막에 반올림.
        for d in D:
            plan[d + 1] = {}
            day_c = 0.0
            for s, sname in enumerate(req.meals):
                # 표기 순서 고정: 주식 → 국 → 주찬 → 부찬 → 김치 (영양사 확정 2026-08-12).
                # 같은 카테고리 안에서는 메뉴명 순 — 같은 해에 대해 출력이 항상 같도록.
                chosen = sorted(
                    (m for m in M if solver.Value(x[m, d, s])),
                    key=lambda m: (mt.category_sort_key(menus[m].category), menus[m].name))
                plan[d + 1][sname] = [menus[m].name for m in chosen]
                day_c += sum(menus[m].calories for m in chosen)
            daily_kcal[d + 1] = round(day_c, 1)
        total_cost = round(sum(menus[m].cost_won for m in M for d in D for s in S
                               if solver.Value(x[m, d, s])))
    objective, hard_breakdown, soft_breakdown = 0.0, None, None
    diversity_breakdown, affinity_breakdown = None, None
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        objective = solver.ObjectiveValue()
        if hard is not None:
            hard_breakdown = hc.evaluate_hard_breakdown(
                solver, x, menus, hard,
                days=req.days, n_meals=len(req.meals),
            )
        soft_breakdown = sc.evaluate_breakdown(
            solver, x, menus, soft,
            days=req.days, n_meals=len(req.meals),
            pref_scores=req.pref_scores,
        )
        diversity_breakdown = scd.evaluate_diversity_breakdown(
            solver, x, menus, div,
            days=req.days, n_meals=len(req.meals),
            weights=req.diversity_weights,
        )
        if any(aff.active_terms.values()):
            affinity_breakdown = ma.evaluate_affinity_breakdown(
                solver, x, menus, aff,
                days=req.days, n_meals=len(req.meals),
            )
    return MealPlanResult(
        status=solver.StatusName(status),
        objective=objective,
        # ⚠ solver.WallTime() 만 쓰면 웜스타트 구성 시간이 빠져 SLA 를 과소 보고한다.
        wall_time=solver.WallTime() + hint_seconds,
        warm_start_seconds=round(hint_seconds, 2),
        plan=plan, daily_kcal=daily_kcal, total_cost=total_cost,
        hard_breakdown=hard_breakdown,
        soft_breakdown=soft_breakdown,
        diversity_breakdown=diversity_breakdown,
        affinity_breakdown=affinity_breakdown,
    )
# ===========================================================================
# (4) 출력
# ===========================================================================
def print_result(res: MealPlanResult, req: MealPlanRequest):
    print("=" * 60)
    print(f"풀이 상태: {res.status}")
    if not res.plan:
        print("해 없음 (제약 충돌 여부 확인 필요)")
        return
    ws_note = f" (그중 초기해 {res.warm_start_seconds:.2f}초)" if res.warm_start_seconds else ""
    print(f"연산 {res.wall_time:.2f}초{ws_note}  ·  Soft 목적점수 {res.objective:,.0f}")
    print("=" * 60)
    for day, meals in res.plan.items():
        print(f"\n[{day}일차] (총 {res.daily_kcal[day]}kcal · 참고)")
        for mname, picks in meals.items():
            print(f"  {mname}: {', '.join(picks)}")
    print(f"\n1인 {req.days}일 총 식재료비(참고): {res.total_cost:,}원")
    # Hard 준수 지표 — 칼로리·예산·알레르기(pmy)
    if res.hard_breakdown:
        print("\n[Hard·칼로리/예산/알레르기]")
        for info in res.hard_breakdown["per_day"]:
            kmark = "OK" if info["kcal_ok"] else "위반"
            bmark = "" if info["budget_ok"] is None else (" · 예산 OK" if info["budget_ok"] else " · 예산 위반")
            print(f"  · {info['day']}일: {info['kcal']}kcal({kmark}) · {info['cost']}원{bmark}")
        print(f"  · 배제 메뉴 {res.hard_breakdown['excluded_menu_count']}종 · 편성 청정: {res.hard_breakdown['excluded_clean']}")
        for nut, info in (res.hard_breakdown.get("nutrient_max") or {}).items():
            mark = "OK" if info["all_ok"] else "위반"
            print(f"  · {nut} 일 상한 {info['limit']:,.0f} → 최대 {info['max_day']:,.0f} ({mark})")
        pr = res.hard_breakdown.get("pairing") or {}
        if pr:
            print(f"  · 주식 없는 끼니 {len(pr['meals_without_staple'])}건 · "
                  f"부적합 궁합 {len(pr['incompatible_pairs'])}건")
    # Soft 항별 지표 — 제공빈도·기호도(ksm)
    if res.soft_breakdown:
        print("\n[Soft·제공빈도/기호도]")
        for ftype, info in res.soft_breakdown["frequency"].items():
            mark = "충족" if info["satisfied"] else "미달"
            op = "≥" if info["kind"] == "min" else "≤"
            print(f"  · {ftype}: {info['count']}회 (목표 {op}{info['target']}) → {mark}")
        print(f"  · 기호도 점수: {res.soft_breakdown['preference_score']}")
    # Soft 항별 지표 — 다양성·제철·나트륨당(nyc)
    if res.diversity_breakdown:
        print("\n[Soft·다양성/제철/나트륨당]")
        for k, v in res.diversity_breakdown.items():
            print(f"  · {k}: {v}")
    # Soft 항별 지표 — 메뉴 어울림(B6 Phase 2)
    if res.affinity_breakdown:
        ab = res.affinity_breakdown
        print("\n[Soft·메뉴 어울림]")
        print(f"  · 활성 축: {ab['active_terms']} · 조리법 커버리지 {ab['method_coverage']}종")
        print(f"  · 같은 조리법 중복 끼니 {ab['method_duplicate_count']}건(감점 축) "
              f"· 전 조리법 중복 {ab['method_duplicate_meals_all']}건 "
              f"· 어울림 점수 {ab['affinity_score']}")
        print(f"  · 한 끼 같은 조리법 상한 {ab['method_max_per_meal']}접시 "
              f"· 초과 {ab['method_over_limit_count']}건")
        for e in ab["method_over_limit_meals"][:5]:
            print(f"    - [상한초과] {e['day']}일 {e['meal_index']}끼: {e['method']} "
                  f"{e['count']}접시 — {', '.join(e['menus'])} ({e['score']})")
        for e in ab["method_duplicate_meals"][:5]:
            print(f"    - {e['day']}일 {e['meal_index']}끼: {e['method']} "
                  f"{', '.join(e['menus'])} ({e['score']})")
# ===========================================================================
# (5) CLI
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description="OptiMeal CSP 모델 정의 코드 (기본 골조)")
    ap.add_argument("--days", type=int, default=7, help="급식 일수 (기본 7)")
    ap.add_argument("--month", type=int, default=None, help="제철 기준 월")
    ap.add_argument("--sodium-max", type=float, default=2000.0,
                    help="1일 나트륨 상한 mg (기본 2000=WHO 성인 권고, 0이면 미적용)")
    ap.add_argument("--time-limit", type=float, default=60.0,
                    help="총 소요 한도 초 (기본 60=NFR-02 31일 SLA). 초기해 구성 시간 포함")
    ap.add_argument("--no-warm-start", action="store_true",
                    help="롤링 웜스타트(초기해) 비활성 — 효과 비교 측정용")
    ap.add_argument("--no-main-axis", action="store_true",
                    help="주재료 중복 회피 항 비활성 — 효과·SLA 비교 측정용(B6 Phase 1)")
    ap.add_argument("--no-affinity", action="store_true",
                    help="메뉴 어울림 항 비활성 — 음성 대조·SLA 비교 측정용(B6 Phase 2)")
    ap.add_argument("--profile", default=None,
                    help="급식 대상 프로파일 key (예: middle_mix·senior_mix). "
                         "--list-profiles 로 목록 확인. 주면 열량·나트륨 기준이 여기서 나온다")
    ap.add_argument("--list-profiles", action="store_true",
                    help="급식 대상 프로파일 목록을 출력하고 종료")
    ap.add_argument("--meals", default=None,
                    help="끼니 이름을 쉼표로 (예: '점심' / '점심,저녁' / '아침,점심,저녁'). "
                         "미지정 시 프로파일 기본값, 프로파일도 없으면 3식")
    ap.add_argument("--budget-cap", type=float, default=3500.0,
                    help="식단가 Hard 상한 (1인 1일, 원). 기본 3500, 0이면 예산 상한 미적용")
    ap.add_argument("--budget-floor", type=float, default=0.0,
                    help="식단가 Soft 하한 (1인 1일, 원). 0이면 하한 미적용(현행 기본)")
    args = ap.parse_args()
    profiles = up.load_profiles()
    if args.list_profiles:
        _print_profiles(profiles)
        return
    menus = load_menus(month=args.month)
    # 급식 대상 프로파일 + 끼니 수 → 열량·나트륨 목표.
    #   프로파일을 주면 그 기준이 CLI 기본값(2000kcal·2000mg)을 **덮는다**.
    #   끼니 수를 줄이면 목표도 그만큼 줄어야 한다 — 안 그러면 한 끼에 하루치가 몰린다.
    profile = profiles.get(args.profile) if args.profile else None
    if args.profile and profile is None:
        raise SystemExit(f"[오류] 알 수 없는 프로파일 '{args.profile}'. "
                         f"--list-profiles 로 확인하세요.")
    meals = _resolve_meals(args.meals, profile)
    tg = up.targets_for(profile, meals) if profile else {}
    kcal = tg.get("target_kcal_per_day", 2000.0)
    sodium_max = tg.get("sodium_max_mg_per_day") if profile else args.sodium_max
    if not profile and args.sodium_max:
        sodium_max = args.sodium_max
    # CLI(운영 경로)는 Hard 제약을 켠다(③칼로리·④예산). ②알레르기는 대체식에서 주입.
    # H-2e 나트륨 상한은 값이 실제로 주입되는 이 경로에서만 켠다(결측=배제 정책 때문).
    nutrient_max, nutrient_by_idx = {}, None
    if sodium_max and sodium_max > 0:
        sodium_by_idx, _ = scd.load_nutrition_fields(get_engine(), menus)
        nutrient_max = {"sodium": sodium_max}
        nutrient_by_idx = {"sodium": sodium_by_idx}
    # 주재료 축(B6 Phase 1) — DB 에 있는 메뉴만 채워지고 나머지는 중립.
    main_by_idx = None if args.no_main_axis else scd.load_main_ingredients(get_engine(), menus)
    # 어울림 근거표(B6 Phase 2) — 파일이 없으면 빈 목록이라 표 기반 항이 자동 비활성.
    #   단 조리법 상한·다양성(2026-08-26)은 표가 아니라 규칙이라 표를 빼도 살아 있다.
    #   --no-affinity 는 "어울림 항 전체 OFF" 라는 뜻이므로 규칙 항 가중치도 0 으로 둔다.
    affinity_table = None if args.no_affinity else ma.load_affinity_table()
    affinity_weights = (ma.AffinityWeights(w_method_over_limit=0, w_method_variety=0)
                        if args.no_affinity else None)
    # H-4b·H-4c 는 실제 음식명 기반이라 운영 경로(DB 메뉴)에서 켠다.
    # 식단가 상한·하한(1인 1일, 원). 0/미지정이면 해당 항 비활성.
    budget_cap = args.budget_cap if args.budget_cap and args.budget_cap > 0 else None
    budget_floor = args.budget_floor if args.budget_floor and args.budget_floor > 0 else None
    # 가격 커버리지 점검 — 원가가 전부 0원이면 하한은 매일 '최대 부족분'을 감점해
    #   "너무 쌈" 을 오탐한다(모든 해가 같은 감점이라 선택은 안 바뀌지만 리포트가 거짓이 된다).
    #   가격이 *일부만* 들어온 단계가 더 위험하다 — 가격 있는 메뉴만 불리해진다.
    priced = sum(1 for m in menus if (getattr(m, "cost_won", 0) or 0) > 0)
    if budget_floor and priced == 0:
        print(f"[식단가] ⚠ 원가>0 메뉴 0/{len(menus)}종 — ingredient_price 미적재라 "
              f"하한을 끈다(오탐 방지). 가격 적재 후 다시 켤 것.")
        budget_floor = None
    cfg = hc.HardConstraintConfig(target_kcal_per_day=kcal,
                                  nutrient_max_per_day=nutrient_max,
                                  budget_limit_per_person=budget_cap,
                                  enable_staple_main=True,
                                  enable_menu_pairing=True)
    if tg.get("meal_energy_ratios"):
        cfg.meal_energy_ratios = tg["meal_energy_ratios"]
    req = MealPlanRequest(days=args.days, meals=meals,
                          hard=cfg,
                          hard_nutrient_by_idx=nutrient_by_idx,
                          solver_time_limit=args.time_limit,
                          warm_start=not args.no_warm_start,
                          main_by_idx=main_by_idx,
                          affinity_table=affinity_table,
                          affinity_weights=affinity_weights,
                          budget_floor_won=budget_floor)
    print(f"[식단가] 상한 {f'{budget_cap:,.0f}원' if budget_cap else '미적용'}"
          f" · 하한 {f'{budget_floor:,.0f}원' if budget_floor else '미적용'}"
          f" · 원가>0 메뉴 {priced}/{len(menus)}종")

    print(f"[메뉴 후보 {len(menus)}종"
          f" / 주재료 확보 {len(main_by_idx or {})}종"
          f" / 어울림 근거 {len(affinity_table or [])}행]")
    if profile:
        print(f"[대상] {profile.group_name} · {len(meals)}식({','.join(meals)})"
              f" → {kcal:,.0f}kcal"
              f"{f' · Na≤{sodium_max:,.0f}mg' if sodium_max else ''}")
        print(f"[근거] {tg['basis']} · 출처 {profile.source}")
    print_result(build_and_solve(menus, req), req)


def _resolve_meals(arg: str | None, profile) -> tuple:
    """--meals 인자 > 프로파일 기본 끼니 수 > 3식 순으로 끼니 이름을 정한다.

    끼니 수만 아는 경우(프로파일 default_meals)는 **어느 끼니인지**를 정해야 배분 비율이
    나온다 — 1식은 점심, 2식은 점심·저녁으로 본다(급식 현장 관행).
    """
    if arg:
        names = tuple(m.strip() for m in arg.split(",") if m.strip())
        if names:
            return names
    n = getattr(profile, "default_meals", 3) if profile else 3
    return {1: ("점심",), 2: ("점심", "저녁")}.get(n, up.DEFAULT_MEALS)


def _print_profiles(profiles: dict) -> None:
    """급식 대상 프로파일 목록(영양사가 자기 업장을 고르는 표)."""
    if not profiles:
        print("프로파일 표를 찾지 못했습니다 — data/processed/user_group_profiles.csv 확인")
        return
    print(f"{'key':22s} {'대상':26s} {'1일kcal':>8s} {'단백질g':>7s} "
          f"{'Na상한mg':>9s} {'기본끼니':>6s}  출처")
    for p in profiles.values():
        print(f"{p.profile_key:22s} {p.group_name:26s} {p.daily_kcal:8,.0f} "
              f"{(p.protein_g or 0):7.1f} {(p.sodium_cdrr_mg or 0):9,.0f} "
              f"{p.default_meals:6d}  {p.source}")
if __name__ == "__main__":
    main()