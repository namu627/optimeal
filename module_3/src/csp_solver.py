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
from dataclasses import dataclass, field
from datetime import date
from ortools.sat.python import cp_model
# 제약 모듈 (같은 패키지). 스크립트/모듈 양쪽 실행 지원.
# 모두 동일 규약(모델 주입·키워드 인자·결과 dataclass·evaluate 동반)의 순수 함수 모듈이며,
# 뼈대(build_and_solve)는 이들을 같은 호출식으로 부른다.
#   hc  = pmy(박미연): Hard — 알레르기·칼로리·예산 (제약)
#   sc  = ksm(권성민): Soft — 제공빈도·기호도·식단가 (목적함수)
#   scd = nyc(남유찬): Soft — 다양성·제철·완제품자제·나트륨당저감 (목적함수)
try:
    from . import csp_hard_constraints as hc
    from . import menu_taxonomy as mt
    from . import soft_constraints as sc
    from . import soft_constraints_diversity as scd
except ImportError:  # `python csp_solver.py` 직접 실행 시
    import csp_hard_constraints as hc
    import menu_taxonomy as mt
    import soft_constraints as sc
    import soft_constraints_diversity as scd
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
    diversity_weights: object = None   # scd.DiversityWeights (None=기본값)
@dataclass
class MealPlanResult:
    status: str
    objective: float
    wall_time: float
    plan: dict           # {day: {meal: [menu_name, ...]}}
    daily_kcal: dict     # {day: 총kcal} (참고용)
    total_cost: int      # 1인 총 식재료비 (참고용)
    hard_breakdown: dict = None        # pmy Hard 지표(칼로리·예산·알레르기 준수) — 미적용/미풀이 시 None
    soft_breakdown: dict = None        # ksm Soft 지표(제공빈도·기호도·원가)
    diversity_breakdown: dict = None   # nyc Soft 지표(다양성·제철·나트륨당)
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
    )
    div = scd.add_diversity_soft_objective(
        model, x, menus,
        days=req.days, n_meals=len(req.meals),
        weights=req.diversity_weights,
    )
    model.Maximize(soft.score + div.score)
    # ---------------------- 풀이 및 결과 추출 -----------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.solver_time_limit
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
    objective, hard_breakdown, soft_breakdown, diversity_breakdown = 0.0, None, None, None
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
    return MealPlanResult(
        status=solver.StatusName(status),
        objective=objective,
        wall_time=solver.WallTime(),
        plan=plan, daily_kcal=daily_kcal, total_cost=total_cost,
        hard_breakdown=hard_breakdown,
        soft_breakdown=soft_breakdown,
        diversity_breakdown=diversity_breakdown,
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
    print(f"연산 {res.wall_time:.2f}초  ·  Soft 목적점수 {res.objective:,.0f}")
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
# ===========================================================================
# (5) CLI
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description="OptiMeal CSP 모델 정의 코드 (기본 골조)")
    ap.add_argument("--days", type=int, default=7, help="급식 일수 (기본 7)")
    ap.add_argument("--month", type=int, default=None, help="제철 기준 월")
    ap.add_argument("--sodium-max", type=float, default=2000.0,
                    help="1일 나트륨 상한 mg (기본 2000=WHO 성인 권고, 0이면 미적용)")
    args = ap.parse_args()
    menus = load_menus(month=args.month)
    # CLI(운영 경로)는 Hard 제약을 켠다(③칼로리·④예산). ②알레르기는 대체식에서 주입.
    # H-2e 나트륨 상한은 값이 실제로 주입되는 이 경로에서만 켠다(결측=배제 정책 때문).
    nutrient_max, nutrient_by_idx = {}, None
    if args.sodium_max and args.sodium_max > 0:
        sodium_by_idx, _ = scd.load_nutrition_fields(get_engine(), menus)
        nutrient_max = {"sodium": args.sodium_max}
        nutrient_by_idx = {"sodium": sodium_by_idx}
    # H-4b·H-4c 는 실제 음식명 기반이라 운영 경로(DB 메뉴)에서 켠다.
    req = MealPlanRequest(days=args.days,
                          hard=hc.HardConstraintConfig(nutrient_max_per_day=nutrient_max,
                                                       enable_staple_main=True,
                                                       enable_menu_pairing=True),
                          hard_nutrient_by_idx=nutrient_by_idx)
    print(f"[메뉴 후보 {len(menus)}종]")
    print_result(build_and_solve(menus, req), req)
if __name__ == "__main__":
    main()