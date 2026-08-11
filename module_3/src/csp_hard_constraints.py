# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · Hard Constraint 구현 (Google OR-Tools CP-SAT)

pmy 골조(`csp_solver.py`)에 "덧붙이는" 강제 제약(Hard Constraint) 모듈.
Soft 모듈(`soft_constraints.py`·`soft_constraints_diversity.py`)과 **동일 규약**으로
작성한다 — 순수 함수, 모델 주입, 키워드 인자(days·n_meals), side-channel 데이터 주입 +
`getattr` per-menu 우아한 저하, 결과 dataclass 반환, `evaluate_*` 리포팅 동반.
통합자(build_and_solve)는 Soft 항과 같은 호출식으로 부른다.

────────────────────────────────────────────────────────────────────────────
Hard Constraint (위반 시 식단 무효 — 가능영역 정의). 기준: 제약조건 정의서 H-1~H-4 / FR-11 / ADR-008
  H-1 안전영역 : 배제식품 미사용(H-1a), CCP2 메뉴 끼니당 상한(H-1b)
  H-2 영양기준 : 에너지 ±10%(H-2a, 일 단위) / 탄단지 비율(H-2b, 주 평균) /
                 당류·첨가당 상한(H-2c, 주 평균) / 필수영양소(H-2d, 일 단위) /
                 영양소 상한(H-2e, 일 단위 — 나트륨 등 과잉 위험 영양소)
                 · 열량구성비·당류 비율은 정의서 기준 "주 평균" → ratio_window_days(기본 7) 창 단위로 강제.
                 · 에너지·필수영양소·영양소 상한은 하루 총량 기준(일 단위).
  H-3 법적표시 : 알레르기 편성 배제 (원산지·표시 자체는 데이터 표기 영역)
  H-4 식단구조 : 반상 유형별 필수 구성(opt-in; menu.category taxonomy 일치 필요)
  ★ 식단가(예산): 총 식재료비가 커트라인 초과 시 무조건 아웃. 커트라인 이내 최적화는 Soft.
────────────────────────────────────────────────────────────────────────────

데이터 규약(Soft 와 동일 — 핵심 정리 포인트):
  · MenuItem 기존 필드(calories·cost_won·allergens)는 그대로 읽는다(항상 존재).
  · 그 외 데이터(is_ccp2·ingredients·carb_g·sugar_g·필수영양소 등)는 현재 MenuItem 에 없으므로
    **side-channel 주입(dict/set) + getattr 폴백** 으로 per-menu 처리한다.
  · 활성/비활성은 **명시적 enable 플래그/데이터 제공 여부**로 결정한다.
    (구버전의 `has()`=모든 메뉴 보유 시에만 켜짐 → 한 건이라도 NULL이면 안전제약이 통째로
     꺼지거나, 0.0 기본값이면 도리어 켜져 INFEASIBLE 을 유발 → 그 함정을 제거함)
  · enable 된 제약에서 값이 없는(None) 메뉴는 각주에 명시한 보수적 기본값으로 처리한다.

※ CP-SAT 정수 연산 → 실수(칼로리·단가·영양소)는 SCALE 배수로 정수화. 비율은 나눗셈을 피해
  양변에 총열량을 곱해 선형화한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model


# ===========================================================================
# 설정 자료구조 — Soft 의 SoftWeights/DiversityWeights 와 동일 위상
# ===========================================================================
@dataclass
class HardConstraintConfig:
    """강제 제약(Hard Constraint) 운영 기준값 및 활성 플래그."""

    # ── H-1 안전영역 ───────────────────────────────────────────────
    excluded_foods: set = field(default_factory=set)   # H-1a 배제식품 재료명(있으면 활성)
    ccp2_max_per_meal: int = 1                          # H-1b CCP2 끼니당 최대 개수
    enable_ccp2: bool = False                           # H-1b 활성(ccp2 데이터 필요)

    # ── H-2 영양기준 ───────────────────────────────────────────────
    target_kcal_per_day: float = 2000.0   # H-2a 1일 권장 에너지(kcal)
    kcal_tolerance: float = 0.10          # H-2a 허용 편차 ±10%
    enable_energy: bool = True            # H-2a 활성(calories 항상 존재 → 기본 ON)
    # H-2a' 끼니별 에너지 배분 (PRD §끼니별 영양 배분: 아침30·점심40·저녁30%)
    #   len(meal_energy_ratios) == n_meals 이고 enable_energy 일 때만 적용된다.
    #   끼니 수가 다르면(1끼·2끼 등) 자동 skip → 기존 호출부 동작 불변.
    meal_energy_ratios: tuple = (0.30, 0.40, 0.30)
    meal_ratio_tolerance: float = 0.15    # 끼니별 허용 편차 ±15%(일 단위 ±10%보다 느슨)
    enable_meal_ratio: bool = True
    carb_ratio: tuple = (55.0, 65.0)      # H-2b 탄수화물 55~65%
    protein_ratio: tuple = (7.0, 20.0)    # H-2b 단백질 7~20%
    fat_ratio: tuple = (15.0, 30.0)       # H-2b 지방 15~30%
    enable_macro_ratio: bool = False      # H-2b 활성(탄단지 데이터 필요)
    sugar_max_ratio: float = 20.0         # H-2c 당류 ≤ 총열량 20%
    added_sugar_max_ratio: float = 10.0   # H-2c 첨가당 ≤ 총열량 10%
    enable_sugar_limit: bool = False      # H-2c 활성(당류 데이터 필요)
    essential_nutrient_min: dict = field(default_factory=dict)  # H-2d {영양소명: 1일 최소량}(있으면 활성)
    # H-2e 영양소 1일 상한 {영양소명: 1일 최대량}(있으면 활성). 나트륨 등 **과잉이 위험한** 영양소용.
    #   예: {"sodium": 2000.0} → 하루 총 나트륨 ≤ 2,000mg (WHO 성인 권고).
    #   ⚠ 기본값은 비어 있다(미적용). 켜려면 값이 실제로 주입되는 경로에서만 켤 것 —
    #     nutrient_max_missing='exclude' 기본 정책상 데이터 없이 켜면 전 메뉴가 배제되어 INFEASIBLE.
    nutrient_max_per_day: dict = field(default_factory=dict)
    # H-2e 결측 처리 정책. 상한 제약에서 "값 없음"을 0으로 보면 나트륨 0인 메뉴로 둔갑해
    #   제약이 조용히 무력화된다 → 기본은 배제(exclude). 'zero'는 데이터 완전성이 확인된 경우만.
    nutrient_max_missing: str = "exclude"  # 'exclude' | 'zero'
    ratio_precision: int = 100            # 비율 분모(퍼센트=100). 1000이면 소수 첫째자리까지 반영.
    ratio_window_days: int = 7            # H-2b·H-2c 비율 적용 창(주 평균=7일). 창 단위 평균 비율을 강제.

    # ── H-3 법적표시(알레르기 편성 배제) ───────────────────────────
    excluded_allergens: set = field(default_factory=set)  # 배제 알레르겐(있으면 활성)

    # ── H-4 식단구조(반상 구성) — opt-in ──────────────────────────
    #   {category: (min, max)}. 비어 있으면 미사용. **menu.category 실제 taxonomy와 일치해야 함**
    #   (현 DB taxonomy = 주식/국/찌개/반찬). 이 옵션을 켜면 골조의 기본 끼니구성과 충돌하므로,
    #   build_and_solve 에서 이 옵션 사용 시 골조 구성을 skip 하도록 연동해야 한다(중복 구성 방지).
    #   ★ 현 확정: 골조의 '주식1·국1·반찬2'를 사용(H-4 비활성 유지). 반상(밥·주찬·부찬·김치)은
    #     반찬→주찬/부찬 세분·김치 태깅 등 데이터 보강 후 이 파라미터로 켠다.
    meal_composition: dict = field(default_factory=dict)

    # ── 메뉴 중복 회피 (PRD §Soft "3일 이내 재등장 금지"를 강제 창으로 구현) ──
    #   창(window) 내에서 동일 메뉴를 1회만 허용. 창이 하루 전체 끼니를 포함하므로
    #   "같은 날 점심·저녁 중복"도 함께 막힌다. 0 이면 미적용.
    #   추가 변수 없이 선형 제약만 쓰므로 비용이 싸다(|M|×(days-w+1) 제약).
    menu_repeat_window_days: int = 3

    # ── 식단가(예산 커트라인) ──────────────────────────────────────
    budget_limit_per_person: float | None = 3500.0  # None이면 예산 제약 미적용
    budget_period: str = "day"            # "day": 일별 상한 / "total": 기간 총액 상한

    # ── 공통 ───────────────────────────────────────────────────────
    scale: int = 100                      # 실수→정수 변환 배수


@dataclass
class HardConstraint:
    """add_hard_constraints 결과(리포팅용). Hard는 제약이라 목적함수 score가 없다."""
    config: HardConstraintConfig
    kcal_lo: int = 0                                   # 정수화 칼로리 하한(×SCALE)
    kcal_hi: int = 0                                   # 정수화 칼로리 상한(×SCALE)
    active_terms: dict = field(default_factory=dict)   # {term명: bool}
    excluded_idx: set = field(default_factory=set)     # 편성 배제된 메뉴 인덱스(H-1a·H-3 합산, 리포팅용)
    # H-2e 상한 적용에 실제로 쓴 값 {영양소명: {메뉴인덱스: 양}} — 리포트가 제약과 같은 수를 보게 한다.
    nutrient_values: dict = field(default_factory=dict)


# ===========================================================================
# 핵심 진입점 — Soft add_*_objective 와 동형 시그니처
# ===========================================================================
def add_hard_constraints(
    model,
    x: dict,
    menus: list,
    *,
    days: int,
    n_meals: int,
    config: HardConstraintConfig | None = None,
    # ── side-channel 데이터 주입(선택). None이면 getattr 폴백 → 없으면 각주의 보수적 기본값 ──
    ingredients_by_idx: dict | None = None,   # {m: set(재료명)}   H-1a
    ccp2_menu_ids: set | None = None,          # {menu_id, ...}    H-1b (이 집합의 메뉴를 CCP2로 간주)
    macro_by_idx: dict | None = None,          # {m: {'carb_g','protein_g','fat_g'}} H-2b
    sugar_by_idx: dict | None = None,          # {m: g}            H-2c
    added_sugar_by_idx: dict | None = None,    # {m: g}            H-2c
    nutrient_by_idx: dict | None = None,       # {영양소명: {m: 양}} H-2d
) -> HardConstraint:
    """model / x / menus 에 Hard 제약(H-1~H-4 + 예산)을 추가한다.

    활성 규칙(명시적):
      H-1a 배제식품 : cfg.excluded_foods 가 비어있지 않으면 활성
      H-1b CCP2     : cfg.enable_ccp2=True 또는 ccp2_menu_ids 주입 시 활성
      H-2a 에너지   : cfg.enable_energy (기본 True)
      H-2b 탄단지   : cfg.enable_macro_ratio (데이터 필요 → 기본 False)
      H-2c 당류     : cfg.enable_sugar_limit (데이터 필요 → 기본 False)
      H-2d 필수영양소: cfg.essential_nutrient_min 이 비어있지 않으면 활성
      H-2e 영양소상한: cfg.nutrient_max_per_day 가 비어있지 않으면 활성(값 주입 필수)
      H-3 알레르기  : cfg.excluded_allergens 가 비어있지 않으면 활성
      H-4 식단구조  : cfg.meal_composition 이 비어있지 않으면 활성(taxonomy 일치 필수)
      예산          : cfg.budget_limit_per_person 이 None 이 아니면 활성

    값 없는(None) 메뉴 처리(enable 된 제약 한정, 보수적):
      · 합계형 상한(H-2c 당류): 미상 → 0 기여(데이터 완전성 전제).
      · 비율/최소형(H-2b·H-2d): 미상 → 0 기여(하한 위반 유도 가능 → 완전한 데이터에서만 켤 것).
      · 상한형(H-2e): 미상 → **편성 배제**(cfg.nutrient_max_missing='exclude', 기본).
        상한에서 미상을 0으로 보면 제약이 조용히 무력화되므로 반대 방향으로 보수적이다.
      · CCP2(H-1b): ccp2_menu_ids/getattr 로 True 인 메뉴만 카운트(미상=비CCP2로 간주).
    """
    cfg = config or HardConstraintConfig()
    D, S, M = range(days), range(n_meals), range(len(menus))
    SCALE = cfg.scale
    P = cfg.ratio_precision
    active: dict = {}
    excluded_idx: set = set()

    # side-channel 우선, 없으면 getattr 폴백 (Soft _num/_val 규약과 동일)
    def num(side, m, attr):
        if side is not None and m in side:
            return side[m] or 0.0
        return getattr(menus[m], attr, None) or 0.0

    def raw(side, m, attr):
        """num() 과 달리 **결측(None)을 0으로 뭉개지 않고** 그대로 돌려준다(H-2e 상한용)."""
        if side is not None and m in side:
            return side[m]
        return getattr(menus[m], attr, None)

    def macro(m, key):
        if macro_by_idx is not None and m in macro_by_idx:
            return (macro_by_idx[m] or {}).get(key, 0.0) or 0.0
        return getattr(menus[m], key, None) or 0.0

    def ban(m):  # 전 슬롯 0 고정
        for d in D:
            for s in S:
                model.Add(x[m, d, s] == 0)

    def windows(w):  # 주 평균용: days 를 창(기본 7일) 단위로 분할(마지막 창은 부분 허용)
        d0 = 0
        while d0 < days:
            yield range(d0, min(d0 + w, days))
            d0 += w

    # =======================================================================
    # H-1a 안전영역 — 배제식품
    # =======================================================================
    if cfg.excluded_foods:
        for m in M:
            ing = (ingredients_by_idx.get(m) if ingredients_by_idx and m in ingredients_by_idx
                   else getattr(menus[m], "ingredients", None)) or set()
            if set(ing) & cfg.excluded_foods:
                excluded_idx.add(m)
                ban(m)
    active["excluded_foods"] = bool(cfg.excluded_foods)

    # =======================================================================
    # H-1b 안전영역 — CCP2 메뉴 끼니당 상한
    # =======================================================================
    ccp2_on = cfg.enable_ccp2 or (ccp2_menu_ids is not None)
    if ccp2_on:
        def is_ccp2(m):
            if ccp2_menu_ids is not None:
                return getattr(menus[m], "menu_id", None) in ccp2_menu_ids
            return bool(getattr(menus[m], "is_ccp2", False))
        ccp2_ms = [m for m in M if is_ccp2(m)]
        if ccp2_ms:
            for d in D:
                for s in S:
                    model.Add(sum(x[m, d, s] for m in ccp2_ms) <= cfg.ccp2_max_per_meal)
    active["ccp2"] = ccp2_on

    # =======================================================================
    # H-2a 영양기준 — 에너지 ±tolerance (일 단위: 하루 총 칼로리 기준)
    # =======================================================================
    kcal_lo = kcal_hi = 0
    if cfg.enable_energy:
        kcal_lo = int(cfg.target_kcal_per_day * (1 - cfg.kcal_tolerance) * SCALE)
        kcal_hi = int(cfg.target_kcal_per_day * (1 + cfg.kcal_tolerance) * SCALE)
        for d in D:
            day_kcal = sum(int(menus[m].calories * SCALE) * x[m, d, s] for m in M for s in S)
            model.Add(day_kcal >= kcal_lo)
            model.Add(day_kcal <= kcal_hi)
    active["energy"] = cfg.enable_energy

    # =======================================================================
    # H-2a' 영양기준 — 끼니별 에너지 배분 (아침30·점심40·저녁30%)
    #   끼니 수와 비율 개수가 맞을 때만 적용(1끼·2끼 호출부 보호).
    # =======================================================================
    ratios = tuple(cfg.meal_energy_ratios or ())
    meal_ratio_on = (cfg.enable_energy and cfg.enable_meal_ratio
                     and len(ratios) == n_meals and n_meals > 1)
    if meal_ratio_on:
        for d in D:
            for s, ratio in zip(S, ratios):
                target = cfg.target_kcal_per_day * ratio
                lo = int(target * (1 - cfg.meal_ratio_tolerance) * SCALE)
                hi = int(target * (1 + cfg.meal_ratio_tolerance) * SCALE)
                meal_kcal = sum(int(menus[m].calories * SCALE) * x[m, d, s] for m in M)
                model.Add(meal_kcal >= lo)
                model.Add(meal_kcal <= hi)
    active["meal_energy_ratio"] = meal_ratio_on

    # =======================================================================
    # H-2b 영양기준 — 탄단지 열량 비율 (탄/단 4kcal·g, 지 9kcal·g) · 주 평균
    #   lo% ≤ Σ_win macro_kcal / Σ_win total_kcal ≤ hi%  →  macro·P ≥ lo·total (반대도)
    #   정의서 "열량구성비(주 평균)" → 창(ratio_window_days, 기본 7일) 단위 평균 비율로 강제.
    # =======================================================================
    if cfg.enable_macro_ratio:
        macro_spec = {"carb_g": (4, cfg.carb_ratio),
                      "protein_g": (4, cfg.protein_ratio),
                      "fat_g": (9, cfg.fat_ratio)}
        for win in windows(cfg.ratio_window_days):
            total_kcal = sum(int(menus[m].calories * SCALE) * x[m, d, s]
                             for m in M for d in win for s in S)
            for key, (kpg, (lo, hi)) in macro_spec.items():
                macro_kcal = sum(int(macro(m, key) * kpg * SCALE) * x[m, d, s]
                                 for m in M for d in win for s in S)
                model.Add(macro_kcal * P >= int(round(lo * P / 100)) * total_kcal)
                model.Add(macro_kcal * P <= int(round(hi * P / 100)) * total_kcal)
    active["macro_ratio"] = cfg.enable_macro_ratio

    # =======================================================================
    # H-2c 영양기준 — 당류·첨가당 상한 (당류 4kcal·g) · 주 평균
    #   Σ_win sugar_kcal ≤ max_ratio% · Σ_win total_kcal  (창 단위 평균)
    # =======================================================================
    if cfg.enable_sugar_limit:
        for side, attr, max_ratio in (
            (sugar_by_idx, "sugar_g", cfg.sugar_max_ratio),
            (added_sugar_by_idx, "added_sugar_g", cfg.added_sugar_max_ratio),
        ):
            for win in windows(cfg.ratio_window_days):
                total_kcal = sum(int(menus[m].calories * SCALE) * x[m, d, s]
                                 for m in M for d in win for s in S)
                sugar_kcal = sum(int(num(side, m, attr) * 4 * SCALE) * x[m, d, s]
                                 for m in M for d in win for s in S)
                model.Add(sugar_kcal * P <= int(round(max_ratio * P / 100)) * total_kcal)
    active["sugar_limit"] = cfg.enable_sugar_limit

    # =======================================================================
    # H-2d 영양기준 — 필수영양소 1일 최소량
    # =======================================================================
    if cfg.essential_nutrient_min:
        for nutrient, min_amount in cfg.essential_nutrient_min.items():
            side = nutrient_by_idx.get(nutrient) if nutrient_by_idx else None
            for d in D:
                day_amount = sum(int(num(side, m, nutrient) * SCALE) * x[m, d, s] for m in M for s in S)
                model.Add(day_amount >= int(min_amount * SCALE))
    active["essential_nutrient"] = bool(cfg.essential_nutrient_min)

    # =======================================================================
    # H-2e 영양기준 — 영양소 1일 상한 (나트륨 등 과잉 위험 영양소)
    #   Σ_day amount(m)·x ≤ max_amount.  값이 없는 메뉴는 정책에 따라 배제(기본)한다.
    #   ※ 하한(H-2d)과 반대로, 상한에서 결측을 0으로 두면 "그 영양소가 없는 메뉴"가 되어
    #     제약을 우회하는 통로가 된다. 그래서 결측은 0이 아니라 배제로 처리한다.
    # =======================================================================
    nutrient_values: dict = {}
    if cfg.nutrient_max_per_day:
        exclude_missing = (cfg.nutrient_max_missing or "exclude") == "exclude"
        for nutrient, max_amount in cfg.nutrient_max_per_day.items():
            side = nutrient_by_idx.get(nutrient) if nutrient_by_idx else None
            usable = []
            for m in M:
                v = raw(side, m, nutrient)
                if v is None and exclude_missing:
                    excluded_idx.add(m)
                    ban(m)
                    continue
                usable.append((m, float(v or 0.0)))
            nutrient_values[nutrient] = dict(usable)
            for d in D:
                day_amount = sum(int(v * SCALE) * x[m, d, s] for m, v in usable for s in S)
                model.Add(day_amount <= int(max_amount * SCALE))
    active["nutrient_max"] = bool(cfg.nutrient_max_per_day)

    # =======================================================================
    # H-3 법적표시 — 알레르기 편성 배제
    # =======================================================================
    if cfg.excluded_allergens:
        for m in M:
            al = getattr(menus[m], "allergens", None) or set()
            if set(al) & cfg.excluded_allergens:
                excluded_idx.add(m)
                ban(m)
    active["allergen"] = bool(cfg.excluded_allergens)

    # =======================================================================
    # H-4 식단구조 — 반상 유형별 필수 구성 (opt-in; taxonomy 일치 필수)
    #   주의: 켤 경우 골조의 기본 끼니구성과 중복되지 않도록 build_and_solve 에서 연동 필요.
    # =======================================================================
    if cfg.meal_composition:
        for d in D:
            for s in S:
                for cat, (lo, hi) in cfg.meal_composition.items():
                    picked = sum(x[m, d, s] for m in M if menus[m].category == cat)
                    if lo is not None:
                        model.Add(picked >= lo)
                    if hi is not None:
                        model.Add(picked <= hi)
                for m in M:
                    if menus[m].category not in cfg.meal_composition:
                        model.Add(x[m, d, s] == 0)
    active["meal_composition"] = bool(cfg.meal_composition)

    # =======================================================================
    # 메뉴 중복 회피 — 창(window) 내 동일 메뉴 1회 (PRD "3일 이내 재등장 금지")
    #   창이 하루의 모든 끼니를 포함하므로 같은 날 점심·저녁 중복도 함께 막힌다.
    #   days < window 이면 전 기간을 하나의 창으로 본다.
    # =======================================================================
    w = int(cfg.menu_repeat_window_days or 0)
    if w > 0:
        span = min(w, days)
        for m in M:
            for d0 in range(0, days - span + 1):
                model.Add(
                    sum(x[m, d, s] for d in range(d0, d0 + span) for s in S) <= 1
                )
    active["menu_repeat_window"] = w > 0

    # =======================================================================
    # 식단가(예산) — 커트라인. 초과 식단은 무조건 후보에서 제외(Hard).
    # =======================================================================
    if cfg.budget_limit_per_person is not None:
        if cfg.budget_period == "day":
            cap = int(cfg.budget_limit_per_person * SCALE)
            for d in D:
                day_cost = sum(int(menus[m].cost_won * SCALE) * x[m, d, s] for m in M for s in S)
                model.Add(day_cost <= cap)
        else:  # "total"
            cap = int(cfg.budget_limit_per_person * days * SCALE)
            total_cost = sum(int(menus[m].cost_won * SCALE) * x[m, d, s]
                             for m in M for d in D for s in S)
            model.Add(total_cost <= cap)
    active["budget"] = cfg.budget_limit_per_person is not None

    return HardConstraint(config=cfg, kcal_lo=kcal_lo, kcal_hi=kcal_hi,
                          active_terms=active, excluded_idx=excluded_idx,
                          nutrient_values=nutrient_values)


# ===========================================================================
# 리포팅 — Soft 의 evaluate_*_breakdown 와 동형
# ===========================================================================
def evaluate_hard_breakdown(
    solver,
    x: dict,
    menus: list,
    hard: HardConstraint,
    *,
    days: int,
    n_meals: int,
) -> dict:
    """풀린 해에서 Hard 준수 지표를 사람이 읽을 형태로 요약한다(리포팅용)."""
    M, D, S = range(len(menus)), range(days), range(n_meals)
    cfg = hard.config
    lo = cfg.target_kcal_per_day * (1 - cfg.kcal_tolerance)
    hi = cfg.target_kcal_per_day * (1 + cfg.kcal_tolerance)

    per_day = []
    for d in D:
        # ⚠ 접시별 int() 절단 금지 — 제약은 int(값*SCALE)(소수 2자리)로 걸린다.
        #   접시마다 버리면 하루 접시 수만큼(3끼×4접시=최대 ~12kcal) 과소 집계되어
        #   제약을 만족한 해가 kcal_ok=False 로 오보된다(2026-08-10 재현·수정).
        #   집계는 float 로 하고 표시 직전에만 반올림한다.
        kcal = round(sum(menus[m].calories for m in M for s in S if solver.Value(x[m, d, s])), 1)
        cost = round(sum(menus[m].cost_won for m in M for s in S if solver.Value(x[m, d, s])))
        per_day.append({
            "day": d + 1,
            "kcal": kcal,
            "kcal_ok": (lo <= kcal <= hi) if cfg.enable_energy else None,
            "cost": cost,
            "budget_ok": (cost <= cfg.budget_limit_per_person)
            if (cfg.budget_limit_per_person is not None and cfg.budget_period == "day") else None,
        })

    allergen_clean = True
    if hard.excluded_idx:
        allergen_clean = not any(
            solver.Value(x[m, d, s]) for m in hard.excluded_idx for d in D for s in S
        )

    return {
        "per_day": per_day,
        "kcal_bounds": (lo, hi) if cfg.enable_energy else None,
        "excluded_menu_count": len(hard.excluded_idx),
        "excluded_clean": allergen_clean,   # 배제 대상(배제식품·알레르기) 편성 안 됨
        "active_terms": hard.active_terms,
        "meal_kcal": _meal_kcal_report(solver, x, menus, hard, days=days, n_meals=n_meals),
        "menu_repeat": _repeat_report(solver, x, menus, hard, days=days, n_meals=n_meals),
        "nutrient_max": _nutrient_max_report(solver, x, menus, hard, days=days, n_meals=n_meals),
    }


def _nutrient_max_report(solver, x, menus, hard, *, days, n_meals) -> dict:
    """H-2e 영양소 일 상한 실측 — {영양소: {limit, per_day:[{day, amount, ok}], max_day}}.

    ⚠ 제약이 쓴 값(hard.nutrient_values)을 그대로 재사용한다. 리포트가 DB를 다시 조회해
      다른 값을 쓰면 정상 식단이 위반으로 보일 수 있다(2026-08-10 칼로리 절단 사례와 동종).
    """
    if not hard.active_terms.get("nutrient_max"):
        return {}
    M, D, S = range(len(menus)), range(days), range(n_meals)
    out = {}
    for nutrient, limit in hard.config.nutrient_max_per_day.items():
        vals = hard.nutrient_values.get(nutrient, {})
        per_day = []
        for d in D:
            amount = sum(vals.get(m, 0.0) for m in M for s in S if solver.Value(x[m, d, s]))
            per_day.append({"day": d + 1, "amount": round(amount, 1), "ok": amount <= limit})
        out[nutrient] = {
            "limit": limit,
            "per_day": per_day,
            "max_day": max((i["amount"] for i in per_day), default=0.0),
            "all_ok": all(i["ok"] for i in per_day),
        }
    return out


def _meal_kcal_report(solver, x, menus, hard, *, days, n_meals) -> list:
    """끼니별 kcal 과 목표 밴드 준수 여부(H-2a')를 요약한다."""
    cfg = hard.config
    if not hard.active_terms.get("meal_energy_ratio"):
        return []
    M, D, S = range(len(menus)), range(days), range(n_meals)
    out = []
    for d in D:
        for s, ratio in zip(S, cfg.meal_energy_ratios):
            kcal = round(sum(menus[m].calories for m in M if solver.Value(x[m, d, s])), 1)
            target = cfg.target_kcal_per_day * ratio
            lo = target * (1 - cfg.meal_ratio_tolerance)
            hi = target * (1 + cfg.meal_ratio_tolerance)
            out.append({"day": d + 1, "meal_index": s, "kcal": kcal,
                        "target": round(target, 1), "ok": lo <= kcal <= hi})
    return out


def _repeat_report(solver, x, menus, hard, *, days, n_meals) -> dict:
    """메뉴 중복 실측 — 창 제약이 실제로 지켜졌는지 확인한다."""
    if not hard.active_terms.get("menu_repeat_window"):
        return {"window_days": 0, "violations": [], "max_same_menu_count": None}
    M, D, S = range(len(menus)), range(days), range(n_meals)
    placed: dict[int, list] = {}
    for m in M:
        for d in D:
            for s in S:
                if solver.Value(x[m, d, s]):
                    placed.setdefault(m, []).append(d)
    span = min(int(hard.config.menu_repeat_window_days), days)
    violations = []
    for m, ds in placed.items():
        ds.sort()
        for a, b in zip(ds, ds[1:]):
            if b - a < span:
                violations.append({"menu": menus[m].name, "days": [a + 1, b + 1]})
    return {
        "window_days": span,
        "violations": violations,
        "max_same_menu_count": max((len(v) for v in placed.values()), default=0),
    }