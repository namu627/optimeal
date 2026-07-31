# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · Soft Constraint 구현 — 다양성 · 제철 · 나트륨당 저감

ksm(권성민) 팀장의 `soft_constraints.py` 와 **동일한 규약**으로 작성한, pmy 골조
(`csp_solver.py`)의 목적함수 훅에 "덧붙이는" Soft 제약 모듈.

담당 범위 (FR-11 Soft / ADR-002 Scoring Function 中):
  (1) 다양성 — 조리법 3종↑(일 단위) · 주재료/색/맛 중복 회피
  (2) 제철   — 제철 식재료 점수(season_score) 높은 식단 선호
  (3) 나트륨당 저감 — Hard 상한 이하라도 총 나트륨·당류가 낮은 식단 선호

범위 밖(다른 Soft 담당): 제공빈도 · 기호도 · 식단가(ksm), 메뉴 중복 회피는 별개 항목.
  → 동일 규약("클수록 좋음")이라 `add_diversity_soft_objective(...).score` 를
    ksm `add_soft_objective(...).score` 와 **그냥 더해** `model.Maximize(...)` 하면 된다.

설계 규약(ksm 모듈과 동일):
  · 모든 항 "클수록 좋음" 부호 → 통합자는 최대화(Maximize)만.
  · CP-SAT 목적함수는 정수-선형이어야 하므로 float(season_score 0~1)은 정수 계수로 스케일.
  · 데이터 없으면 infeasible 되지 말고 중립(0)으로 **우아한 저하**.
    - color_category 전량 NULL / recipe_ingredient_map 0건 / 맛 스키마 부재 →
      해당 항은 자동 비활성(0)이 되며, 데이터 적재 시 **코드 변경 없이** 켜진다.
  · 가중치 기본값(정수, ksm 척도 계열): w_cook ≥ w_color=w_main ≥ w_taste ≥ w_season ≥ w_na=w_sugar.
    통합 시 ksm 항과의 최종 정규화는 팀 조정 사항(정의서 §6 참조).

MenuItem 확장 규약(우아한 저하로 지금도 동작):
  - name·category·menu_id·colors·season_score : pmy MenuItem 기존 필드 그대로 사용.
  - cooking_method(str) : recipe.primary_method_id 로더가 채우면 권위값으로 사용,
                          없으면 메뉴명 키워드로 분류(현 상태에서도 동작).
  - main_ingredient(str) : recipe_ingredient_map(role='주재료') 적재 시 사용, 없으면 중립.
  - sodium(float, mg) / sugar(float, g) : nutrition_recipe 직속 컬럼. 로더 SELECT 추가 필요.
  - taste(str) : 스키마 부재 → 항상 중립(정의서 §5 미구현 사유 명시).

기준 문서: 제약조건 정의서(H/Soft) / FR-11 / ADR-002 Scoring / ADR-004(COMMERCIAL).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 순수 함수 모듈: OR-Tools 모델 객체는 호출부에서 주입받는다(테스트 용이).


# ===========================================================================
# (A) 조리법 분류 — 메뉴명 키워드(우회) 또는 로더 권위값(cooking_method)
# ===========================================================================
# recipe_ingredient_map/recipe 미적재 상태에서도 동작하도록, 메뉴명 키워드로 조리법을
# 근사 분류한다(ksm 의 classify_food_types 와 동일한 우회 전략). 로더가 menu.cooking_method
# 를 채우면 그 권위값을 우선 사용한다.
#
# ※ 라벨 어휘는 DB cooking_method.method_name(8종: 끓이기·볶음·찜·구이·무침·조림·튀김·삶기)에
#   맞춘다. 그래야 DB 정답 라벨(load_cooking_methods)과 키워드 라벨이 한 식단에 섞여도
#   동일 조리법을 같은 값으로 세어 이중 계상되지 않는다. (예: 국·탕류는 DB의 '끓이기'로 통일)
#   '부침'은 DB 8종에 없으나 키워드 경로 전용 라벨이라 충돌 없음. DB '삶기'는 키워드 미대응.
#
# 우선순위(먼저 매칭되는 유형 채택): 튀김 > 무침 > 끓이기 > 조림 > 찜 > 구이 > 부침 > 볶음.
# (예: '오징어볶음튀김' 같은 복합명은 더 특징적인 '튀김'을 우선.)
COOKING_METHOD_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("튀김", ("튀김", "까스", "카츠", "가스", "크로켓", "고로케", "강정", "탕수", "너겟", "너깃", "프라이", "후라이")),
    ("무침", ("무침", "나물", "생채", "숙채", "겉절이", "샐러드", "냉채", "초무침")),
    ("끓이기", ("국", "탕", "찌개", "전골", "육개장", "지리", "개장")),
    ("조림", ("조림", "장조림", "조린", "찜조림")),
    ("찜", ("찜", "수육", "보쌈", "찐")),
    ("구이", ("구이", "구운", "스테이크", "너비아니", "떡갈비")),
    ("부침", ("부침", "지짐", "빈대떡", "동그랑땡", "전유어", "적")),
    ("볶음", ("볶음", "볶이", "잡채")),
)


def classify_cooking_methods(
    menus: list,
    keywords: tuple[tuple[str, tuple[str, ...]], ...] | None = None,
    override_by_idx: dict[int, str] | None = None,
) -> dict[int, str]:
    """메뉴 후보를 조리법 유형으로 분류한다(다양성 계산용, 메뉴당 1개 대표 유형).

    메뉴별 우선순위: (1) override_by_idx 주입값(예: DB `load_cooking_methods` 결과)
    > (2) menu.cooking_method 속성 > (3) 메뉴명 키워드. 셋 다 없으면 미상 → dict 제외.
    즉 DB 값이 있는 메뉴는 정답 조리법, 없는 메뉴는 이름 키워드로 **메뉴별 병합**된다.

    Args:
        menus: MenuItem 리스트.
        keywords: (유형명, 키워드들) 우선순위 튜플. None이면 기본값.
        override_by_idx: {인덱스: 조리법명} 주입(부분 가능). 없는 인덱스는 폴백.

    Returns:
        {메뉴 인덱스: 조리법유형명}. 미상 메뉴는 dict 에 없음 → 카운트에서 중립 제외.
    """
    kw = keywords or COOKING_METHOD_KEYWORDS
    override_by_idx = override_by_idx or {}
    result: dict[int, str] = {}
    for m_idx, menu in enumerate(menus):
        # 1) 주입값(DB 등) 우선
        if m_idx in override_by_idx and override_by_idx[m_idx]:
            result[m_idx] = str(override_by_idx[m_idx])
            continue
        # 2) MenuItem 속성 권위값
        authoritative = getattr(menu, "cooking_method", None)
        if authoritative:
            result[m_idx] = str(authoritative)
            continue
        # 3) 메뉴명 키워드 우회 분류
        name = getattr(menu, "name", "") or ""
        for type_name, patterns in kw:
            if any(p in name for p in patterns):
                result[m_idx] = type_name
                break
    return result


# ===========================================================================
# (B) 설정 자료구조
# ===========================================================================
@dataclass
class DiversityWeights:
    """다양성·제철·나트륨당 Soft 가중치 및 스케일 파라미터(모두 정수).

    "클수록 좋음" 규약: reward 항은 +가중치, penalty 항은 코드에서 -가중치로 적용.
    float 값(season_score·sodium·sugar)은 아래 스케일 단위로 정수화한다.
    """
    # 다양성
    w_cook: int = 8            # 일별 조리법 3종 미달 1종당 감점
    w_color: int = 6           # 일별 색감 3종 미달 1종당 감점
    w_main: int = 6            # 주재료 반복(cap 초과) 1회당 감점
    w_taste: int = 4           # 맛 반복(cap 초과) 1회당 감점
    # 제철
    w_season: int = 3          # 제철점수 스케일 단위당 가점
    # 나트륨당 저감
    w_na: int = 1              # 나트륨 SODIUM_UNIT(mg)당 감점
    w_sugar: int = 1           # 당류 SUGAR_UNIT(g)당 감점

    # 목표치
    cook_target_per_day: int = 3      # 하루 조리법 종수 목표(≥3)
    color_target_per_day: int = 3     # 하루 색감 종수 목표(≥3): FR-11 '한 끼 3색↑'의 일 단위 근사
    main_cap_per_week: float = 2.0    # 동일 주재료 주당 허용 횟수(초과분 감점)
    taste_cap_per_week: float = 3.0   # 동일 맛 주당 허용 횟수(초과분 감점)

    # 정수화 스케일 단위 (항 간 균형: 항당 기여를 대략 0~25 범위로 맞춤)
    #   ※ season_scale 를 크게 두면 제철 항이 다른 항을 압도한다(가중치 정규화).
    #     기본값은 균형 캘리브레이션이며, 실데이터로 재조정 권장(정의서 §6).
    season_scale: int = 20     # season_score(0~1) × 20 → 항당 0~20
    sodium_unit_mg: int = 100  # 나트륨 100mg = w_na 점 (항당 대략 2~9)
    sugar_unit_g: int = 2      # 당류 2g = w_sugar 점 (항당 대략 0~15)


@dataclass
class DiversitySoftObjective:
    """add_diversity_soft_objective 결과. score 를 통합 목적함수에 더해 최대화한다."""
    score: object                          # cp_model LinearExpr (Maximize 대상)
    cook_short_vars: dict = field(default_factory=dict)   # {day: 조리법 부족량 IntVar}
    color_short_vars: dict = field(default_factory=dict)  # {day: 색감 부족량 IntVar}
    main_excess_vars: dict = field(default_factory=dict)  # {main_ingredient: 초과량 IntVar}
    taste_excess_vars: dict = field(default_factory=dict) # {taste: 초과량 IntVar}
    active_terms: dict = field(default_factory=dict)       # {term명: 활성여부} 리포팅용
    cooking_methods: dict = field(default_factory=dict)    # 분류 결과(리포팅용)


# ===========================================================================
# (C) 목적함수 조립 — 핵심 진입점
# ===========================================================================
def scale_targets(per_week: float, days: int) -> int:
    """주당 목표를 지평(days) 총량으로 스케일한다(7일=그대로, 31일=round(x*days/7))."""
    return int(round(per_week * days / 7.0))


def _new_used_bool(model, name: str, placed: list):
    """placed(BoolVar 리스트) 중 하나라도 1이면 1이 되는 지시변수(OR)를 만든다."""
    u = model.NewBoolVar(name)
    if placed:
        model.AddMaxEquality(u, placed)   # u = max(placed) = OR
    else:
        model.Add(u == 0)
    return u


def add_diversity_soft_objective(
    model,
    x: dict,
    menus: list,
    *,
    days: int,
    n_meals: int,
    weights: DiversityWeights | None = None,
    cooking_methods: dict[int, str] | None = None,
    sodium_by_idx: dict[int, float] | None = None,
    sugar_by_idx: dict[int, float] | None = None,
    main_by_idx: dict[int, str] | None = None,
    taste_by_idx: dict[int, str] | None = None,
) -> DiversitySoftObjective:
    """다양성·제철·나트륨당 Soft 목적을 모델에 더하고 점수식을 돌려준다.

    데이터 조달 규약(ksm `add_soft_objective` 와 동일):
      · MenuItem 에 이미 있는 값(name·category·colors·season_score)은 그대로 읽는다.
      · MenuItem 에 없는 값(sodium·sugar·main_ingredient·taste)은 **side-channel 인자**로
        주입받는다(ksm 의 pref_scores·commercial_menu_ids 와 동일 패턴). 인자를 주지 않으면
        `getattr` 폴백 → 없으면 중립(우아한 저하). DB 에서 채우려면 §(E) 헬퍼 사용.

    Args:
        model: cp_model.CpModel 인스턴스.
        x: 결정변수 dict {(m,d,s): BoolVar} (pmy 골조와 동일 키).
        menus: MenuItem 리스트.
        days: 급식 일수. n_meals: 끼니 수.
        weights: DiversityWeights. None이면 기본값.
        cooking_methods: {인덱스: 조리법명} 부분 주입(예: load_cooking_methods 결과).
            주입된 메뉴는 그 값, 나머지는 메뉴명 키워드로 병합 분류.
        sodium_by_idx/sugar_by_idx: {인덱스: 값} 주입(mg/g). None이면 getattr 폴백.
        main_by_idx/taste_by_idx: {인덱스: 주재료명/맛} 주입. None이면 getattr 폴백.

    Returns:
        DiversitySoftObjective. `.score` 를 ksm 항 등과 합산해 Maximize.
    """
    w = weights or DiversityWeights()
    M = range(len(menus))
    D = range(days)
    S = range(n_meals)
    active: dict = {}

    # side-channel 주입값 우선, 없으면 MenuItem 속성 폴백 → 없으면 중립.
    def _num(side, m, attr):
        if side is not None and m in side:
            return side[m] or 0.0
        return getattr(menus[m], attr, None) or 0.0

    def _val(side, m, attr):
        if side is not None and m in side:
            return side[m]
        return getattr(menus[m], attr, None)

    main_values = {m: _val(main_by_idx, m, "main_ingredient") for m in M}
    taste_values = {m: _val(taste_by_idx, m, "taste") for m in M}

    # ------------------------------------------------------------------ #
    # (1a) 다양성 — 조리법 3종↑ (일 단위)                                  #
    # ------------------------------------------------------------------ #
    methods = classify_cooking_methods(menus, override_by_idx=cooking_methods)
    method_labels = sorted(set(methods.values()))
    cook_short_vars: dict = {}
    cook_terms = []
    if method_labels:
        active["cook"] = True
        for d in D:
            used_vars = []
            for g in method_labels:
                placed = [x[m, d, s] for m in M if methods.get(m) == g for s in S]
                used_vars.append(_new_used_bool(model, f"cookused_{d}_{g}", placed))
            distinct = model.NewIntVar(0, len(method_labels), f"cookdistinct_{d}")
            model.Add(distinct == sum(used_vars))
            short = model.NewIntVar(0, w.cook_target_per_day, f"cookshort_{d}")
            model.Add(short >= w.cook_target_per_day - distinct)
            cook_short_vars[d] = short
            cook_terms.append(short)
    else:
        active["cook"] = False
    cook_penalty = sum(cook_terms) if cook_terms else 0

    # ------------------------------------------------------------------ #
    # (1b) 다양성 — 색감 중복 회피(일별 색 종수 ↑)                         #
    #   color_category 전량 NULL 이면 자동 비활성(우아한 저하).            #
    # ------------------------------------------------------------------ #
    all_colors: set = set()
    for menu in menus:
        all_colors |= set(getattr(menu, "colors", None) or set())
    color_short_vars: dict = {}
    color_terms = []
    if all_colors:
        active["color"] = True
        color_list = sorted(all_colors)
        for d in D:
            used_vars = []
            for c in color_list:
                placed = [x[m, d, s] for m in M
                          if c in (getattr(menus[m], "colors", None) or set()) for s in S]
                used_vars.append(_new_used_bool(model, f"colorused_{d}_{c}", placed))
            distinct = model.NewIntVar(0, len(color_list), f"colordistinct_{d}")
            model.Add(distinct == sum(used_vars))
            short = model.NewIntVar(0, w.color_target_per_day, f"colorshort_{d}")
            model.Add(short >= w.color_target_per_day - distinct)
            color_short_vars[d] = short
            color_terms.append(short)
    else:
        active["color"] = False
    color_penalty = sum(color_terms) if color_terms else 0

    # ------------------------------------------------------------------ #
    # (1c) 다양성 — 주재료 중복 회피(지평 내 동일 주재료 cap 초과 감점)     #
    #   main_ingredient 미적재면 자동 비활성.                             #
    # ------------------------------------------------------------------ #
    main_excess_vars = _duplicate_excess_terms(
        model, x, main_values, D, S, M,
        cap_per_week=w.main_cap_per_week, days=days, tag="main")
    active["main"] = bool(main_excess_vars)
    main_penalty = sum(main_excess_vars.values()) if main_excess_vars else 0

    # ------------------------------------------------------------------ #
    # (1d) 다양성 — 맛 중복 회피(스키마 부재 → 현재 항상 비활성)           #
    # ------------------------------------------------------------------ #
    taste_excess_vars = _duplicate_excess_terms(
        model, x, taste_values, D, S, M,
        cap_per_week=w.taste_cap_per_week, days=days, tag="taste")
    active["taste"] = bool(taste_excess_vars)
    taste_penalty = sum(taste_excess_vars.values()) if taste_excess_vars else 0

    # ------------------------------------------------------------------ #
    # (2) 제철 — season_score 높은 식단 선호(가점). 전량 0이면 중립.        #
    # ------------------------------------------------------------------ #
    season_terms = []
    for m in M:
        coef = int(round((getattr(menus[m], "season_score", 0.0) or 0.0) * w.season_scale))
        if coef:
            season_terms += [coef * x[m, d, s] for d in D for s in S]
    season_reward = sum(season_terms) if season_terms else 0
    active["season"] = bool(season_terms)

    # ------------------------------------------------------------------ #
    # (3) 나트륨·당 저감 — 총량 낮을수록 가점(감점 최소화). 없으면 중립.    #
    # ------------------------------------------------------------------ #
    na_terms, sugar_terms = [], []
    for m in M:
        na = _num(sodium_by_idx, m, "sodium")
        sg = _num(sugar_by_idx, m, "sugar")
        na_coef = int(round(na / w.sodium_unit_mg))
        sg_coef = int(round(sg / w.sugar_unit_g))
        if na_coef:
            na_terms += [na_coef * x[m, d, s] for d in D for s in S]
        if sg_coef:
            sugar_terms += [sg_coef * x[m, d, s] for d in D for s in S]
    sodium_penalty = sum(na_terms) if na_terms else 0
    sugar_penalty = sum(sugar_terms) if sugar_terms else 0
    active["sodium"] = bool(na_terms)
    active["sugar"] = bool(sugar_terms)

    # ------------------------------------------------------------------ #
    # 총점 = +제철 − (조리법·색·주재료·맛 위반) − (나트륨·당)  (최대화)     #
    # ------------------------------------------------------------------ #
    score = (
        (w.w_season * season_reward)
        - (w.w_cook * cook_penalty)
        - (w.w_color * color_penalty)
        - (w.w_main * main_penalty)
        - (w.w_taste * taste_penalty)
        - (w.w_na * sodium_penalty)
        - (w.w_sugar * sugar_penalty)
    )

    return DiversitySoftObjective(
        score=score,
        cook_short_vars=cook_short_vars,
        color_short_vars=color_short_vars,
        main_excess_vars=main_excess_vars,
        taste_excess_vars=taste_excess_vars,
        active_terms=active,
        cooking_methods=methods,
    )


def _duplicate_excess_terms(model, x, values_by_idx, D, S, M, *, cap_per_week, days, tag):
    """값(주재료/맛)별로 지평 내 등장 횟수의 cap 초과분 IntVar 를 만든다.

    값이 하나도 없으면 빈 dict 반환(우아한 저하 → 항 비활성).
    values_by_idx: {인덱스: 라벨 or None}.
    """
    groups: dict = {}
    for m in M:
        val = values_by_idx.get(m)
        if val:
            groups.setdefault(str(val), []).append(m)
    if not groups:
        return {}
    cap = scale_targets(cap_per_week, days)
    ub = max(1, days * len(list(S)) * 4)
    excess_vars: dict = {}
    for val, members in groups.items():
        count = sum(x[m, d, s] for m in members for d in D for s in S)
        exc = model.NewIntVar(0, ub, f"{tag}excess_{val}")
        model.Add(exc >= count - cap)
        excess_vars[val] = exc
    return excess_vars


# ===========================================================================
# (D) 리포팅 — 풀이 결과에서 항별 지표 재계산 (Scoring Function 정의서용)
# ===========================================================================
def evaluate_diversity_breakdown(
    solver,
    x: dict,
    menus: list,
    soft: DiversitySoftObjective,
    *,
    days: int,
    n_meals: int,
    weights: DiversityWeights | None = None,
) -> dict:
    """풀린 해에서 다양성·제철·나트륨당 지표를 사람이 읽을 형태로 요약한다."""
    w = weights or DiversityWeights()
    M = range(len(menus))
    D = range(days)
    S = range(n_meals)

    # 일별 조리법 종수
    cook_by_day = {}
    for d in D:
        seen = set()
        for m in M:
            g = soft.cooking_methods.get(m)
            if g and any(int(solver.Value(x[m, d, s])) for s in S):
                seen.add(g)
        cook_by_day[d + 1] = {"distinct": len(seen), "target": w.cook_target_per_day,
                              "satisfied": len(seen) >= w.cook_target_per_day, "methods": sorted(seen)}

    # 일별 색감 종수
    color_by_day = {}
    if soft.active_terms.get("color"):
        for d in D:
            seen = set()
            for m in M:
                cols = getattr(menus[m], "colors", None) or set()
                if cols and any(int(solver.Value(x[m, d, s])) for s in S):
                    seen |= set(cols)
            color_by_day[d + 1] = {"distinct": len(seen), "target": w.color_target_per_day,
                                   "satisfied": len(seen) >= w.color_target_per_day}

    # 주재료/맛 반복
    def _repeat_report(attr):
        counts: dict = {}
        for m in M:
            val = getattr(menus[m], attr, None)
            if not val:
                continue
            c = sum(int(solver.Value(x[m, d, s])) for d in D for s in S)
            if c:
                counts[str(val)] = counts.get(str(val), 0) + c
        return counts

    total_season = sum(
        (getattr(menus[m], "season_score", 0.0) or 0.0) * int(solver.Value(x[m, d, s]))
        for m in M for d in D for s in S)
    total_sodium = sum(
        (getattr(menus[m], "sodium", 0.0) or 0.0) * int(solver.Value(x[m, d, s]))
        for m in M for d in D for s in S)
    total_sugar = sum(
        (getattr(menus[m], "sugar", 0.0) or 0.0) * int(solver.Value(x[m, d, s]))
        for m in M for d in D for s in S)

    return {
        "active_terms": soft.active_terms,
        "cooking_diversity": cook_by_day,
        "color_diversity": color_by_day,
        "main_ingredient_counts": _repeat_report("main_ingredient"),
        "taste_counts": _repeat_report("taste"),
        "total_season_score": round(total_season, 3),
        "total_sodium_mg": round(total_sodium, 1),
        "total_sugar_g": round(total_sugar, 1),
    }


# ===========================================================================
# (E) DB 헬퍼 — MenuItem 에 없는 값을 side-channel 로 조달 (선택적, DB 가동 시)
#   ksm `load_preference_scores` 와 동일 패턴: menu_id→인덱스 매핑 후 dict 반환.
#   → add_diversity_soft_objective(..., sodium_by_idx=..., main_by_idx=...) 로 주입.
#   DB 미가동/미적재 시 호출부는 이 헬퍼를 건너뛰면 되고(주입 None), 항은 중립 저하.
# ===========================================================================
def load_nutrition_fields(engine, menus: list) -> tuple[dict[int, float], dict[int, float]]:
    """nutrition_recipe.sodium/sugar 를 menu_id 로 조회해 (sodium_by_idx, sugar_by_idx) 반환."""
    from sqlalchemy import text

    id_to_idx = {getattr(m, "menu_id", None): i for i, m in enumerate(menus)}
    ids = [mid for mid in id_to_idx if mid is not None]
    sodium: dict[int, float] = {}
    sugar: dict[int, float] = {}
    if not ids:
        return sodium, sugar
    q = text("SELECT nutrition_id, sodium, sugar FROM nutrition_recipe "
             "WHERE nutrition_id = ANY(:ids)")
    with engine.connect() as conn:
        for r in conn.execute(q, {"ids": ids}).mappings():
            idx = id_to_idx.get(r["nutrition_id"])
            if idx is None:
                continue
            if r["sodium"] is not None:
                sodium[idx] = float(r["sodium"])
            if r["sugar"] is not None:
                sugar[idx] = float(r["sugar"])
    return sodium, sugar


def load_cooking_methods(engine, menus: list) -> dict[int, str]:
    """recipe.primary_method_id → cooking_method.method_name 을 menu_id 별로 조회.

    recipe 에 연결된 메뉴(nutrition_recipe_id NOT NULL)만 정답 조리법을 얻는다.
    미연결 메뉴는 dict 에 없음 → add_diversity_soft_objective 가 메뉴명 키워드로 폴백.
    """
    from sqlalchemy import text

    id_to_idx = {getattr(m, "menu_id", None): i for i, m in enumerate(menus)}
    ids = [mid for mid in id_to_idx if mid is not None]
    methods: dict[int, str] = {}
    if not ids:
        return methods
    q = text(
        """
        SELECT nr.nutrition_id AS menu_id, cm.method_name AS method
        FROM nutrition_recipe nr
        JOIN recipe r          ON r.nutrition_recipe_id = nr.nutrition_id
        JOIN cooking_method cm ON cm.method_id = r.primary_method_id
        WHERE nr.nutrition_id = ANY(:ids)
        """
    )
    with engine.connect() as conn:
        for r in conn.execute(q, {"ids": ids}).mappings():
            idx = id_to_idx.get(r["menu_id"])
            if idx is not None and r["method"] and idx not in methods:
                methods[idx] = r["method"]   # 첫 조리법을 대표로
    return methods


def load_main_ingredients(engine, menus: list) -> dict[int, str]:
    """recipe_ingredient_map(ingredient_role='주재료') → menu_id 별 대표 주재료명 반환.

    ★ 정식(canonical) 경로. recipe_ingredient_map 이 적재되면 이걸 우선 사용.
    현재는 recipe_ingredient_map 이 0행이라 빈 dict 반환 → 주재료 항 중립(우아한 저하).
    지금 당장 쓰려면 `load_main_ingredients_from_training` 우회 경로 참고.
    """
    from sqlalchemy import text

    id_to_idx = {getattr(m, "menu_id", None): i for i, m in enumerate(menus)}
    ids = [mid for mid in id_to_idx if mid is not None]
    main: dict[int, str] = {}
    if not ids:
        return main
    q = text(
        """
        SELECT nr.nutrition_id AS menu_id, i.ingredient_name AS main
        FROM nutrition_recipe nr
        JOIN recipe r                  ON r.nutrition_recipe_id = nr.nutrition_id
        JOIN recipe_ingredient_map rim ON rim.recipe_id = r.recipe_id
                                       AND rim.ingredient_role = '주재료'
        JOIN ingredient i              ON i.ingredient_id = rim.ingredient_id
        WHERE nr.nutrition_id = ANY(:ids)
        """
    )
    with engine.connect() as conn:
        for r in conn.execute(q, {"ids": ids}).mappings():
            idx = id_to_idx.get(r["menu_id"])
            if idx is not None and r["main"] and idx not in main:
                main[idx] = r["main"]   # 첫 주재료를 대표로
    return main


def load_main_ingredients_from_training(engine, menus: list) -> dict[int, str]:
    """[우회 경로] recipe_ingredient_map(0행) 대신 ml_training_dataset 에서 주재료명 조달.

    검증(2026-07-30, DB_COOKING/DB_주재료)으로 확인된 경로:
      ml_training_dataset(ingredient_role='주재료', 89건) .ingredient_id
        → ingredient.ingredient_name (재료명)
      ml_training_dataset.base_recipe_id → recipe.recipe_id → recipe.nutrition_recipe_id (메뉴)

    ⚠ 검증 결과(2026-07-30): **현재 메뉴 커버리지 0건.** 주재료 89행이 참조하는 recipe 가
      전부 nutrition_recipe_id IS NULL(메뉴 미연결)이라, 이 경로로 메뉴 단위 주재료를
      하나도 얻지 못한다(SQL 은 정상, 데이터가 없음 → 우아한 저하로 중립). 정식 소스
      (recipe_ingredient_map) 또는 recipe↔nutrition_recipe 매핑이 확장되면 활성.
      한 메뉴에 주재료가 여럿이면 첫 값을 대표로 사용.
    """
    from sqlalchemy import text

    id_to_idx = {getattr(m, "menu_id", None): i for i, m in enumerate(menus)}
    ids = [mid for mid in id_to_idx if mid is not None]
    main: dict[int, str] = {}
    if not ids:
        return main
    q = text(
        """
        SELECT r.nutrition_recipe_id AS menu_id, i.ingredient_name AS main
        FROM ml_training_dataset m
        JOIN recipe r     ON r.recipe_id = m.base_recipe_id
        JOIN ingredient i ON i.ingredient_id = m.ingredient_id
        WHERE m.ingredient_role = '주재료'
          AND r.nutrition_recipe_id = ANY(:ids)
        """
    )
    with engine.connect() as conn:
        for r in conn.execute(q, {"ids": ids}).mappings():
            idx = id_to_idx.get(r["menu_id"])
            if idx is not None and r["main"] and idx not in main:
                main[idx] = r["main"]
    return main
