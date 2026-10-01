# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · 식단가 이월(carryover) — 끼니 예산 밴드·맛 균형 Soft 항 + 리포트

왜 필요한가 (2026-09-30):
  · 기존 예산은 하루 상한(budget_period="day")뿐이라, 끼니 예산 B 로 하루 B×M 을 넘는 날이
    하루라도 필요하면 전체가 INFEASIBLE 이 된다. 현장 식단가는 "기간 총액 안에서 어떤 날은
    조금 비싸고 어떤 날은 조금 싸게"(이월) 운영한다.
  · 총액 상한만 두면 한 끼에 몰아 쓰고 다른 끼니를 굶기는 극단 배치가 나올 수 있으므로,
    끼니 단위 밴드와 연속일 균형(원가·나트륨)을 Soft 로 건다.

구성 (B = 1끼 예산, M = 하루 끼니 수, D = 일수):
  (a) 총예산 Hard — 여기서 걸지 않는다. csp_hard_constraints 의 budget_period="total" 을 쓴다
      (budget_limit_per_person = B×M 이면 상한 = B×M×D).
  (a') 끼니 원가 울타리 Hard(옵트인, guard_low·guard_high) — 각 (날짜, 끼니) 원가를 B×guard_low 이상
      B×guard_high 이하로 강제한다. Soft 밴드만으로는 30초 안에 극단 끼니(B 의 0.3배·2배)가 남는 것을
      막지 못해서 추가했다(2026-09-30 가중치 스윕: 어떤 가중치로도 밴드 밖 끼니 ≤15% 미달).
      원 단위 ×100 정수로 걸어 리포트(float 합)와 판정이 어긋나지 않게 한다.
  (b) 끼니 밴드 Soft — 각 (날짜, 끼니) 원가가 B×band_high 초과 / B×band_low 미만이면
      벗어난 금액 cost_unit_won(100원)당 w_band 감점. 앞뒤 순서 제한은 두지 않는다.
  (c) 맛 균형 Soft — 같은 끼니 슬롯의 연속일 원가 차가 B×smooth_cost_ratio 를 넘으면 초과분
      100원당 w_smooth_cost 감점. 하루 나트륨 합의 연속일 차가 나트륨 상한×smooth_sodium_ratio 를
      넘으면 초과분 sodium_unit_mg(100mg)당 w_smooth_sodium 감점(나트륨 값·상한을 줄 때만).
  (d) 리포트 — 끼니별 원가, 최대/최소, B 초과 끼니 수, 연속일 최대 원가 차, 밴드 이탈 수.

규약은 soft_constraints 와 동일: 순수 함수, 모델 주입, 키워드 인자, "클수록 좋음" score.
원가는 접시별로 cost_unit_won 단위 반올림 정수 계수로 선형화한다(soft_constraints 하한항과 같은 방식).
리포트는 반올림 없이 원 단위 float 로 다시 집계한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class CarryoverConfig:
    """이월 모드 설정. per_meal_budget_won(B) 외에는 전부 조정 가능한 기본값."""
    per_meal_budget_won: float                 # B: 1인 1끼 예산(원)
    # (b) 끼니 밴드
    band_low_ratio: float = 0.8                # B×0.8 미만이면 감점(최저가 쏠림 방지)
    band_high_ratio: float = 1.2               # B×1.2 초과면 감점(한 끼 몰아쓰기 방지)
    w_band: int = 2                            # 밴드 이탈 cost_unit_won 당 감점
    # (c) 맛 균형 — 원가
    smooth_cost_ratio: float = 0.25            # 같은 끼니 연속일 원가 차 허용폭 = B×0.25
    w_smooth_cost: int = 1                     # 허용폭 초과 cost_unit_won 당 감점
    # (c) 맛 균형 — 나트륨
    smooth_sodium_ratio: float = 0.25          # 연속일 하루 나트륨 차 허용폭 = 상한×0.25
    w_smooth_sodium: int = 1                   # 허용폭 초과 sodium_unit_mg 당 감점
    sodium_max_per_day: float | None = None    # None 이면 나트륨 균형 항 비활성
    # (a') 끼니 원가 울타리 Hard — B 배수. 둘 다 None 이면 미적용(기존 모델과 동일). 한쪽만 줘도 된다.
    guard_low: float | None = None
    guard_high: float | None = None
    # 정수화 단위
    cost_unit_won: int = 100
    sodium_unit_mg: int = 100


@dataclass
class CarryoverObjective:
    """add_carryover_objective 결과. score 를 통합 목적함수에 더해 최대화한다."""
    score: object
    config: CarryoverConfig
    band_over_vars: dict = field(default_factory=dict)       # {(d,s): IntVar} 상한 초과(단위)
    band_under_vars: dict = field(default_factory=dict)      # {(d,s): IntVar} 하한 미달(단위)
    smooth_cost_vars: dict = field(default_factory=dict)     # {(d,s): IntVar} d-1→d 초과(단위)
    smooth_sodium_vars: dict = field(default_factory=dict)   # {d: IntVar} d-1→d 초과(단위)
    active_terms: dict = field(default_factory=dict)


def guard_bounds_won(cfg: CarryoverConfig) -> tuple[float | None, float | None] | None:
    """울타리(원). 미적용이면 None."""
    if cfg.guard_low is None and cfg.guard_high is None:
        return None
    B = float(cfg.per_meal_budget_won)
    return (B * cfg.guard_low if cfg.guard_low is not None else None,
            B * cfg.guard_high if cfg.guard_high is not None else None)


def add_meal_cost_guard(model, x: dict, menus: list, *, days: int, n_meals: int,
                        low_won: float | None, high_won: float | None) -> None:
    """각 (날짜, 끼니) 원가를 [low_won, high_won] 안으로 Hard 제약한다(원 ×100 정수).

    웜스타트 하루 부분 문제도 같은 함수를 쓴다 — 힌트가 울타리를 모르면 버려진다.
    """
    if low_won is None and high_won is None:
        return
    coef = {m: int(round((getattr(menus[m], "cost_won", 0.0) or 0.0) * 100)) for m in range(len(menus))}
    lo = int(math.ceil(low_won * 100)) if low_won is not None else None
    hi = int(math.floor(high_won * 100)) if high_won is not None else None
    for d in range(days):
        for s in range(n_meals):
            cost = sum(c * x[m, d, s] for m, c in coef.items() if c)
            if lo is not None:
                model.Add(cost >= lo)
            if hi is not None:
                model.Add(cost <= hi)


def _cost_units(menus, m, unit) -> int:
    return int(round((getattr(menus[m], "cost_won", 0.0) or 0.0) / unit))


def add_carryover_objective(
    model,
    x: dict,
    menus: list,
    *,
    days: int,
    n_meals: int,
    config: CarryoverConfig,
    sodium_by_idx: dict | None = None,
) -> CarryoverObjective:
    """끼니 밴드(b)·맛 균형(c) Soft 항을 모델에 더하고 점수식을 돌려준다.

    sodium_by_idx: {메뉴 인덱스: 나트륨 mg}. None 이거나 config.sodium_max_per_day 가 None 이면
        나트륨 균형 항은 비활성. 값이 None 인 메뉴는 0 기여(H-2e 가 결측을 이미 배제한다).
    """
    cfg = config
    D, S, M = range(days), range(n_meals), range(len(menus))
    guard = guard_bounds_won(cfg)
    if guard is not None:
        add_meal_cost_guard(model, x, menus, days=days, n_meals=n_meals, low_won=guard[0], high_won=guard[1])
    unit = max(1, cfg.cost_unit_won)
    B = float(cfg.per_meal_budget_won)
    hi_units = int(round(B * cfg.band_high_ratio / unit))
    lo_units = int(round(B * cfg.band_low_ratio / unit))
    tol_units = int(round(B * cfg.smooth_cost_ratio / unit))
    coef = {m: _cost_units(menus, m, unit) for m in M}
    ub = max(1, sum(max(0, c) for c in coef.values()))   # 한 끼 원가 단위 상한(느슨)

    meal_cost = {(d, s): sum(coef[m] * x[m, d, s] for m in M if coef[m])
                 for d in D for s in S}
    over, under, smooth = {}, {}, {}
    for d in D:
        for s in S:
            o = model.NewIntVar(0, ub, f"co_over_{d}_{s}")
            model.Add(o >= meal_cost[d, s] - hi_units)
            u = model.NewIntVar(0, max(0, lo_units), f"co_under_{d}_{s}")
            model.Add(u >= lo_units - meal_cost[d, s])
            over[d, s], under[d, s] = o, u
            if d > 0:
                e = model.NewIntVar(0, ub, f"co_smooth_{d}_{s}")
                model.Add(e >= meal_cost[d, s] - meal_cost[d - 1, s] - tol_units)
                model.Add(e >= meal_cost[d - 1, s] - meal_cost[d, s] - tol_units)
                smooth[d, s] = e

    na_smooth = {}
    na_active = bool(sodium_by_idx) and cfg.sodium_max_per_day is not None
    if na_active:
        nunit = max(1, cfg.sodium_unit_mg)
        na = {m: int(round((sodium_by_idx.get(m) or 0.0) / nunit)) for m in M}
        na_ub = max(1, sum(max(0, v) for v in na.values()))
        na_tol = int(round(cfg.sodium_max_per_day * cfg.smooth_sodium_ratio / nunit))
        day_na = {d: sum(na[m] * x[m, d, s] for m in M for s in S if na[m]) for d in D}
        for d in D:
            if d == 0:
                continue
            e = model.NewIntVar(0, na_ub, f"co_na_smooth_{d}")
            model.Add(e >= day_na[d] - day_na[d - 1] - na_tol)
            model.Add(e >= day_na[d - 1] - day_na[d] - na_tol)
            na_smooth[d] = e

    score = (
        -cfg.w_band * (sum(over.values()) + sum(under.values()))
        - cfg.w_smooth_cost * sum(smooth.values())
        - cfg.w_smooth_sodium * sum(na_smooth.values())
    )
    return CarryoverObjective(
        score=score, config=cfg,
        band_over_vars=over, band_under_vars=under,
        smooth_cost_vars=smooth, smooth_sodium_vars=na_smooth,
        active_terms={"meal_band": True, "smooth_cost": days > 1, "smooth_sodium": na_active and days > 1,
                      "meal_guard": guard is not None},
    )


def meal_cost_stats(meal_costs: list[dict], per_meal_budget_won: float,
                    band_low_ratio: float = 0.8, band_high_ratio: float = 1.2) -> dict:
    """[{day, meal_index, cost}] → 이월 통계. 솔버 없이 plan 만으로도 쓸 수 있게 분리했다."""
    B = per_meal_budget_won
    costs = [c["cost"] for c in meal_costs]
    by_slot: dict = {}
    for c in meal_costs:
        by_slot.setdefault(c["meal_index"], {})[c["day"]] = c["cost"]
    diffs = [abs(slot[d] - slot[d - 1]) for slot in by_slot.values() for d in slot if d - 1 in slot]
    return {
        "per_meal_budget": B,
        "max_meal_cost": max(costs, default=0),
        "min_meal_cost": min(costs, default=0),
        "meals_over_budget": sum(c > B for c in costs),
        "meals_above_band": sum(c > B * band_high_ratio for c in costs),
        "meals_below_band": sum(c < B * band_low_ratio for c in costs),
        "max_consecutive_cost_diff": max(diffs, default=0),
    }


def _guard_report(cfg: CarryoverConfig, meal_costs: list[dict]) -> dict | None:
    g = guard_bounds_won(cfg)
    if g is None:
        return None
    lo, hi = g
    outside = sum((lo is not None and c["cost"] < lo) or (hi is not None and c["cost"] > hi) for c in meal_costs)
    return {"min_won": round(lo) if lo is not None else None, "max_won": round(hi) if hi is not None else None,
            "meals_outside": outside}


def evaluate_carryover_breakdown(
    solver,
    x: dict,
    menus: list,
    obj: CarryoverObjective,
    *,
    days: int,
    n_meals: int,
    total_budget_won: float | None = None,
    sodium_by_idx: dict | None = None,
) -> dict:
    """풀린 해에서 끼니별 원가 배열과 이월 통계를 만든다(원 단위 float 재집계)."""
    cfg = obj.config
    M = range(len(menus))
    meal_costs, total = [], 0.0
    for d in range(days):
        for s in range(n_meals):
            cost = sum((getattr(menus[m], "cost_won", 0.0) or 0.0) for m in M if solver.Value(x[m, d, s]))
            total += cost   # 총액은 반올림 전 값으로 — Hard 리포트(budget_total)와 같은 수가 나오게
            meal_costs.append({"day": d + 1, "meal_index": s, "cost": round(cost)})
    total = round(total)
    out = {
        "meal_costs": meal_costs,
        **meal_cost_stats(meal_costs, cfg.per_meal_budget_won, cfg.band_low_ratio, cfg.band_high_ratio),
        "total_cost": total,
        "total_budget": total_budget_won,
        "total_headroom": (round(total_budget_won - total) if total_budget_won is not None else None),
        "band": [round(cfg.per_meal_budget_won * cfg.band_low_ratio), round(cfg.per_meal_budget_won * cfg.band_high_ratio)],
        "guard": _guard_report(cfg, meal_costs),
        "active_terms": obj.active_terms,
        "penalty_units": {
            "band": sum(int(solver.Value(v)) for v in obj.band_over_vars.values())
                    + sum(int(solver.Value(v)) for v in obj.band_under_vars.values()),
            "smooth_cost": sum(int(solver.Value(v)) for v in obj.smooth_cost_vars.values()),
            "smooth_sodium": sum(int(solver.Value(v)) for v in obj.smooth_sodium_vars.values()),
        },
    }
    if sodium_by_idx:
        day_na = [round(sum((sodium_by_idx.get(m) or 0.0) for m in M for s in range(n_meals)
                            if solver.Value(x[m, d, s]))) for d in range(days)]
        out["daily_sodium"] = day_na
        out["max_consecutive_sodium_diff"] = max(
            (abs(day_na[d] - day_na[d - 1]) for d in range(1, days)), default=0)
    return out
