# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · Soft Constraint 구현 — 제공빈도 · 기호도 · 식단가 최적화

pmy 골조(`csp_solver.py`)의 목적함수 훅에 "덧붙이는" 형태의 Soft 제약 모듈.
세 가지 소프트 목적을 CP-SAT **정수-선형 점수식**으로 만들어, 통합자가
`model.Maximize(score)` 로 합산할 수 있게 단일 선형식을 돌려준다.

담당 범위 (FR-11 Soft / ADR-002 Scoring Function 中):
  (1) 제공빈도  — 잡곡밥·나물 주3회↑, 튀김·가공식품 주2회↓ (양방향 soft)
  (2) 기호도    — group 의 선호(+)/기피(−) 재료를 반영 (constraints 테이블)
  (3) 식단가    — 실행가능 범위 내 총 식재료비 하향 최적화

범위 밖(다른 Soft 담당): 색감 다양성(colors) · 제철(season_score) · 메뉴 중복 회피.
  → 이들도 같은 패턴으로 별도 항을 더해 `add_soft_objective` 결과와 합산 가능.

설계 규약:
  · 모든 항은 "클수록 좋음" 부호로 정규화 → 통합자는 최대화(Maximize)만 하면 됨.
  · CP-SAT 목적함수는 정수-선형이어야 하므로 float(원가·기호도)는 정수 계수로 스케일.
  · 가중치 기본값: w_freq=10 ≫ w_pref=5 ≫ w_cost=1 (빈도 최우선, 원가 미세조정).

기준 문서: FR-11, PRD §7 Scoring, ADR-008(캘리브레이션 위상에서도 CSP는 병행).
"""
from __future__ import annotations

from dataclasses import dataclass

# 순수 함수 모듈: OR-Tools 모델 객체는 호출부에서 주입받는다(테스트 용이).


# ===========================================================================
# (A) 유형 분류 — 메뉴명/카테고리/조달형태 기반 (제공빈도용)
# ===========================================================================
# 실제 급식 메뉴명(data/raw 매칭쌍 CSV)에 근거한 키워드 사전.
# 예: 오징어튀김·닭튀김·생선까스·치즈감자크로켓 / 감자채햄볶음(가공) 등.
DEFAULT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    # 잡곡밥: 주식 중 잡곡·통곡물 계열
    "잡곡밥": (
        "잡곡", "현미", "보리", "흑미", "오곡", "귀리", "기장", "수수",
        "찰보리", "발아", "콩밥", "혼합곡",
    ),
    # 나물: 무침·숙채·생채 계열 반찬
    "나물": ("나물", "무침", "숙채", "생채", "겉절이"),
    # 튀김: 튀김·전분옷 계열
    "튀김": (
        "튀김", "까스", "카츠", "가스", "크로켓", "고로케",
        "프라이", "후라이", "탕수", "강정", "너겟", "너깃",
    ),
    # 가공식품: 상품형(햄·소시지 등). ingredient_type='COMMERCIAL' 이 더 권위 있는 신호.
    "가공식품": (
        "햄", "소시지", "비엔나", "맛살", "게맛살", "어묵", "오뎅",
        "베이컨", "스팸", "런천", "만두", "핫도그", "떡갈비", "동그랑땡",
    ),
}


def classify_food_types(
    menus: list,
    keywords: dict[str, tuple[str, ...]] | None = None,
    commercial_menu_ids: set | None = None,
) -> dict[int, set]:
    """메뉴 후보를 제공빈도 규칙용 유형으로 분류한다.

    Args:
        menus: MenuItem 리스트(속성 name·category·menu_id 사용).
        keywords: 유형→키워드 사전. None이면 DEFAULT_TYPE_KEYWORDS.
        commercial_menu_ids: ingredient_type='COMMERCIAL' 재료를 포함한 menu_id 집합
            (DB에서 조회 시 주입). 해당 메뉴는 '가공식품' 유형으로 확정 추가.

    Returns:
        {메뉴 인덱스: {유형명, ...}}. 한 메뉴가 복수 유형일 수 있다.
    """
    kw = keywords or DEFAULT_TYPE_KEYWORDS
    commercial_menu_ids = commercial_menu_ids or set()
    result: dict[int, set] = {}
    for m_idx, menu in enumerate(menus):
        name = getattr(menu, "name", "") or ""
        types: set = set()
        for type_name, patterns in kw.items():
            if any(p in name for p in patterns):
                types.add(type_name)
        # 잡곡밥은 주식 카테고리에서만 유효(오분류 방지: '콩나물밥'은 주식이면 인정)
        if "잡곡밥" in types and getattr(menu, "category", None) != "주식":
            types.discard("잡곡밥")
        # 조달형태 권위 신호: COMMERCIAL 재료 포함 → 가공식품 확정
        if getattr(menu, "menu_id", None) in commercial_menu_ids:
            types.add("가공식품")
        if types:
            result[m_idx] = types
    return result


# ===========================================================================
# (B) 설정 자료구조
# ===========================================================================
@dataclass(frozen=True)
class FrequencyRule:
    """제공빈도 규칙 하나. rate 는 '주(7일)당' 목표 횟수."""
    food_type: str            # classify_food_types 의 유형명
    kind: str                 # 'min'(이상) | 'max'(이하)
    per_week: float           # 주당 목표 횟수 (예: 3.0, 2.0)


# 잡곡밥·나물 주3회 이상, 튀김·가공식품 주2회 이하 (기본 규칙)
DEFAULT_FREQUENCY_RULES: tuple[FrequencyRule, ...] = (
    FrequencyRule("잡곡밥", "min", 3.0),
    FrequencyRule("나물", "min", 3.0),
    FrequencyRule("튀김", "max", 2.0),
    FrequencyRule("가공식품", "max", 2.0),
)


@dataclass
class SoftWeights:
    """Soft 목적함수 가중치 및 스케일 파라미터.

    정수-선형 목적함수를 위해 float 값(원가·기호도)은 정수 계수로 스케일한다.
    """
    w_freq: int = 10          # 제공빈도 위반 1회당 감점
    w_pref: int = 5           # 기호도 1점(선호 재료 1건)당 가점
    w_cost: int = 1           # 원가 COST_UNIT(원)당 감점
    cost_unit_won: int = 100  # 원가 정규화 단위(원). 100원 = w_cost 점


# ===========================================================================
# (C) 목적함수 조립 — 핵심 진입점
# ===========================================================================
@dataclass
class SoftObjective:
    """add_soft_objective 결과. score 를 통합 목적함수에 더해 최대화한다."""
    score: object                        # cp_model LinearExpr (Maximize 대상)
    freq_penalty_vars: dict              # {food_type: 위반량 IntVar}
    total_cost_expr: object              # 총 식재료비 정수 선형식(원)
    pref_expr: object                    # 기호도 점수 선형식
    food_types: dict[int, set]           # 분류 결과(리포팅용)


def scale_targets(per_week: float, days: int) -> int:
    """주당 목표를 지평(days) 총량으로 스케일한다.

    7일이면 그대로, 31일이면 round(per_week * days/7). 다주간 min/max 는
    지평 총량 근사이며, 엄밀한 롤링 7일 윈도우는 향후 과제(모듈 확장 지점).
    """
    return int(round(per_week * days / 7.0))


def add_soft_objective(
    model,
    x: dict,
    menus: list,
    *,
    days: int,
    n_meals: int,
    weights: SoftWeights | None = None,
    freq_rules: tuple[FrequencyRule, ...] = DEFAULT_FREQUENCY_RULES,
    pref_scores: dict[int, float] | None = None,
    food_types: dict[int, set] | None = None,
    commercial_menu_ids: set | None = None,
) -> SoftObjective:
    """제공빈도·기호도·식단가 Soft 목적을 모델에 더하고 점수식을 돌려준다.

    Args:
        model: cp_model.CpModel 인스턴스.
        x: 결정변수 dict {(m,d,s): BoolVar} (pmy 골조와 동일 키).
        menus: MenuItem 리스트.
        days: 급식 일수. n_meals: 끼니 수.
        weights: SoftWeights. None이면 기본값.
        freq_rules: 제공빈도 규칙들.
        pref_scores: {메뉴 인덱스: 기호도 점수(선호+/기피−)}. None이면 중립(0).
        food_types: 사전 계산된 분류 결과. None이면 내부 classify_food_types.
        commercial_menu_ids: 가공식품 확정용 menu_id 집합.

    Returns:
        SoftObjective. `.score` 를 다른 Soft 항과 합산해 Maximize.
    """
    w = weights or SoftWeights()
    M = range(len(menus))
    D = range(days)
    S = range(n_meals)
    if food_types is None:
        food_types = classify_food_types(menus, commercial_menu_ids=commercial_menu_ids)
    pref_scores = pref_scores or {}

    # --- (1) 제공빈도: 유형별 count 와 위반량(shortfall/excess) ---------------
    freq_penalty_terms = []
    freq_penalty_vars: dict = {}
    # 위반량 IntVar 상한: 한 끼니 최대 배치 수를 넉넉히 4로 잡은 지평 총 배치 상한.
    viol_ub = max(1, days * n_meals * 4)
    for rule in freq_rules:
        members = [m for m in M if rule.food_type in food_types.get(m, ())]
        count = sum(x[m, d, s] for m in members for d in D for s in S)
        target = scale_targets(rule.per_week, days)
        # 위반량 IntVar: 0 이상, 지평 총량 상한
        viol = model.NewIntVar(0, viol_ub, f"freqviol_{rule.food_type}")
        if rule.kind == "min":
            # 부족분: viol >= target - count  (count 가 target 이상이면 0)
            model.Add(viol >= target - count)
        else:  # 'max'
            # 초과분: viol >= count - target
            model.Add(viol >= count - target)
        freq_penalty_vars[rule.food_type] = viol
        freq_penalty_terms.append(viol)
    freq_penalty = sum(freq_penalty_terms) if freq_penalty_terms else 0

    # --- (2) 기호도: 선호(+)/기피(−) 재료 반영 --------------------------------
    pref_expr = sum(
        int(round(pref_scores.get(m, 0.0))) * x[m, d, s]
        for m in M for d in D for s in S
        if int(round(pref_scores.get(m, 0.0))) != 0
    )

    # --- (3) 식단가: 총 식재료비(원) 정수 선형식 -------------------------------
    total_cost_expr = sum(
        int(round(getattr(menus[m], "cost_won", 0.0) or 0.0)) * x[m, d, s]
        for m in M for d in D for s in S
    )
    # 원가를 정규화 단위로 나눈 정수 계수(선형 유지 위해 계수에 미리 반영)
    cost_penalty = sum(
        (int(round((getattr(menus[m], "cost_won", 0.0) or 0.0) / w.cost_unit_won))) * x[m, d, s]
        for m in M for d in D for s in S
    )

    # --- 총점 = -빈도위반*w_freq + 기호도*w_pref - 원가*w_cost (최대화) --------
    score = (-w.w_freq * freq_penalty) + (w.w_pref * pref_expr) + (-w.w_cost * cost_penalty)

    return SoftObjective(
        score=score,
        freq_penalty_vars=freq_penalty_vars,
        total_cost_expr=total_cost_expr,
        pref_expr=pref_expr,
        food_types=food_types,
    )


# ===========================================================================
# (D) 리포팅 — 풀이 결과에서 항별 지표 재계산
# ===========================================================================
def evaluate_breakdown(
    solver,
    x: dict,
    menus: list,
    soft: SoftObjective,
    *,
    days: int,
    n_meals: int,
    freq_rules: tuple[FrequencyRule, ...] = DEFAULT_FREQUENCY_RULES,
    pref_scores: dict[int, float] | None = None,
) -> dict:
    """풀린 해에서 제공빈도 충족 여부·기호도·원가를 사람이 읽을 형태로 요약한다."""
    pref_scores = pref_scores or {}
    M = range(len(menus))
    D = range(days)
    S = range(n_meals)

    freq_report = {}
    for rule in freq_rules:
        members = [m for m in M if rule.food_type in soft.food_types.get(m, ())]
        cnt = sum(int(solver.Value(x[m, d, s])) for m in members for d in D for s in S)
        target = scale_targets(rule.per_week, days)
        ok = cnt >= target if rule.kind == "min" else cnt <= target
        freq_report[rule.food_type] = {
            "kind": rule.kind, "target": target, "count": cnt, "satisfied": ok,
        }

    pref_total = sum(
        int(round(pref_scores.get(m, 0.0))) * int(solver.Value(x[m, d, s]))
        for m in M for d in D for s in S
    )
    cost_total = sum(
        int(round(getattr(menus[m], "cost_won", 0.0) or 0.0)) * int(solver.Value(x[m, d, s]))
        for m in M for d in D for s in S
    )
    return {
        "frequency": freq_report,
        "preference_score": pref_total,
        "total_cost_won": cost_total,
    }


# ===========================================================================
# (E) DB 헬퍼 — 기호도 점수 조회 (선택적, DB 가동 시)
# ===========================================================================
def load_preference_scores(engine, menus: list, group_id: int | None) -> dict[int, float]:
    """constraints(선호/기피) 테이블에서 메뉴별 기호도 점수를 계산한다.

    선호 재료 1건당 +1, 기피 재료 1건당 −1 (severity '절대금지'는 알레르기/Hard 영역이라 제외).
    group_id 가 None 이거나 데이터가 없으면 전부 0(중립)을 돌려준다 — 우아한 저하.

    Note:
        DB 미가동/미적재 시 호출부는 이 함수를 건너뛰고 pref_scores=None(중립)로 진행하면 된다.
    """
    from sqlalchemy import text

    menu_ids = [getattr(m, "menu_id", None) for m in menus]
    id_to_idx = {mid: i for i, mid in enumerate(menu_ids) if mid is not None}
    scores: dict[int, float] = {}

    query = text(
        """
        SELECT nr.nutrition_id AS menu_id,
               SUM(CASE WHEN c.constraint_type = '선호' THEN 1
                        WHEN c.constraint_type = '기피' THEN -1 ELSE 0 END) AS pref
        FROM nutrition_recipe nr
        JOIN recipe r                   ON r.nutrition_recipe_id = nr.nutrition_id
        JOIN recipe_ingredient_map rim  ON rim.recipe_id = r.recipe_id
        JOIN constraints c              ON c.ingredient_id = rim.ingredient_id
        WHERE c.constraint_type IN ('선호', '기피')
          AND (c.group_id = :gid OR c.group_id IS NULL)
        GROUP BY nr.nutrition_id
        """
    )
    with engine.connect() as conn:
        for row in conn.execute(query, {"gid": group_id}).mappings():
            idx = id_to_idx.get(row["menu_id"])
            if idx is not None and row["pref"]:
                scores[idx] = float(row["pref"])
    return scores
