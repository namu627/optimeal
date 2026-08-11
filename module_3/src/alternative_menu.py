# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · 대체 메뉴 분기 처리 (알레르기 대응)

간트 WBS '대체 메뉴 분기 처리' / PRD FR-11 '공통식 + 대체식 트랙 분리'.

무엇을 하나:
  · 공통식(build_and_solve 로 생성한 일반 식단)은 그대로 둔다.  ← "대체 메뉴로 통일 X"
  · 알레르기 그룹별로, 공통식에서 그 그룹 알레르겐이 든 접시만 대체 메뉴로 슬롯 교체.
  · 대체 메뉴는 **기본식과 같은 규칙으로 선정**한다:
      (1) 하드 = 필터 : 같은 카테고리·알레르겐 없음 + 바꿔도 그날 칼로리 밴드/예산이 안 깨지는 후보만
      (2) 소프트 = 점수: 남은 안전 후보를 제철·색감·완제품회피·나트륨당·원가로 점수화 → 최고점 선정
      (3) 하드 지키는 후보가 없으면 '영양사 확인' 플래그(unresolved).

설계 규약(솔버·하드·소프트와 동일 위상):
  · 순수 함수. OR-Tools 불필요(이미 풀린 공통식 plan 위에서 동작 = 슬롯 스왑, 재풀이 안 함).
  · MenuItem 은 덕타이핑(.menu_id·.name·.category·.calories·.cost_won·.allergens·.colors·.season_score).
  · 소프트 점수는 CP-SAT 전역 목적함수의 **접시 단위 근사**다(재풀이를 피하기 위한 의도된 절충).
  · 전제: 공통식은 '일반식'(build_and_solve 를 excluded_allergens=∅ 로 푼 것)이어야 교체 대상이 생긴다.

범위(간트 행81): 알레르기 대체만. 기저질환 대체는 하드 H-5 + 영양소 데이터 준비 후 확장(본 파일 범위 밖).
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ===========================================================================
# 입력/설정 자료구조
# ===========================================================================
@dataclass
class AllergyGroup:
    """동일 알레르겐 프로파일을 공유하는 인원 묶음(PRD '그룹별 적용')."""
    label: str
    allergens: set = field(default_factory=set)
    count: int = 0


@dataclass
class AltScoreWeights:
    """대체 후보 소프트 점수 가중치(기본식 소프트 규칙의 접시 단위 근사)."""
    w_season: float = 3.0        # 제철 점수(season_score 0~1) 가점
    w_color: float = 2.0         # 이 끼니에 새 색을 더할 때마다 가점(색감 다양성)
    w_commercial: float = 5.0    # 완제품/튀김 메뉴 감점
    w_cost: float = 0.01         # 원가 1원당 감점(낮을수록 좋음)
    w_sodium: float = 0.0        # 나트륨 1mg당 감점(데이터 주입 시)
    w_sugar: float = 0.0         # 당류 1g당 감점(데이터 주입 시)


# 완제품/튀김 판별 키워드(권위 신호는 ADR-004 COMMERCIAL / commercial_menu_ids 주입).
_COMMERCIAL_KEYWORDS = (
    "햄", "소시지", "비엔나", "맛살", "게맛살", "어묵", "오뎅", "베이컨", "스팸", "런천",
    "만두", "핫도그", "너겟", "너깃", "크로켓", "고로케", "돈가스", "돈까스", "카츠",
    "튀김", "까스", "탕수", "강정", "프라이", "후라이",
)


# ===========================================================================
# 결과 자료구조
# ===========================================================================
@dataclass
class Substitution:
    day: int
    meal: str
    category: str
    original: str
    alternative: str | None      # None = 하드 지키는 안전 대체 없음 → 영양사 확인
    hit_allergens: set
    score: float = 0.0           # 선정된 대체의 소프트 점수(참고)


@dataclass
class AlternativeMenu:
    group: AllergyGroup
    plan: dict
    substitutions: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    daily_kcal: dict = field(default_factory=dict)
    total_cost: int = 0


# ===========================================================================
# 대체 후보: 하드 필터 + 소프트 점수
# ===========================================================================
def _is_commercial(m, commercial_menu_ids):
    if commercial_menu_ids and getattr(m, "menu_id", None) in commercial_menu_ids:
        return True
    name = getattr(m, "name", "") or ""
    return any(k in name for k in _COMMERCIAL_KEYWORDS)


def _score_candidate(c, meal_colors, w, *, sodium_by_id, sugar_by_id, commercial_menu_ids):
    """대체 후보 c 의 소프트 점수(클수록 좋음)."""
    colors = set(getattr(c, "colors", set()) or set())
    new_colors = colors - meal_colors
    cid = getattr(c, "menu_id", None)
    sodium = (sodium_by_id or {}).get(cid, 0.0) or 0.0
    sugar = (sugar_by_id or {}).get(cid, 0.0) or 0.0
    score = 0.0
    score += w.w_season * (getattr(c, "season_score", 0.0) or 0.0)
    score += w.w_color * len(new_colors)
    score -= w.w_commercial * (1.0 if _is_commercial(c, commercial_menu_ids) else 0.0)
    score -= w.w_cost * (getattr(c, "cost_won", 0.0) or 0.0)
    score -= w.w_sodium * sodium
    score -= w.w_sugar * sugar
    return score


def _sodium_of(menu, sodium_by_id):
    """메뉴의 나트륨(mg). 주입 dict 우선, 없으면 속성 폴백. **모르면 None**(0으로 뭉개지 않음)."""
    if sodium_by_id is not None:
        v = sodium_by_id.get(getattr(menu, "menu_id", None))
        if v is not None:
            return float(v)
    v = getattr(menu, "sodium", None)
    return float(v) if v is not None else None


def _pick_alternative(orig, menus, allergens, *, exclude_names, meal_colors,
                      day_kcal_wo_orig, band, budget_left, weights,
                      sodium_by_id, sugar_by_id, commercial_menu_ids,
                      sodium_left=None):
    """orig 를 대체할 최고점 안전 메뉴를 고른다. 하드 지키는 후보가 없으면 None.

    하드 필터: 같은 카테고리 · 알레르겐 없음 · 끼니 내 중복 아님
             · (밴드 있으면) 교체 후 그날 총 칼로리가 밴드 안 · (예산 있으면) 잔여 예산 이내
             · (나트륨 상한 있으면) 교체 후 그날 총 나트륨이 상한 이내 [H-2e]
    소프트 점수: _score_candidate 최댓값 선정.

    ※ sodium_left 가 주어졌는데 후보의 나트륨을 모르면 그 후보는 **탈락**시킨다.
      상한 제약에서 미상을 통과시키면 공통식이 지킨 상한을 대체식이 조용히 깨뜨린다
      (csp_hard_constraints H-2e 의 결측=배제 정책과 동일 방향).
    """
    ocat = getattr(orig, "category", None)
    cands = [m for m in menus
             if getattr(m, "category", None) == ocat
             and not (set(getattr(m, "allergens", set()) or set()) & allergens)
             and getattr(m, "name", None) not in exclude_names]
    valid = []
    for c in cands:
        new_day = day_kcal_wo_orig + (getattr(c, "calories", 0.0) or 0.0)
        if band is not None and not (band[0] <= new_day <= band[1]):
            continue
        if budget_left is not None and (getattr(c, "cost_won", 0.0) or 0.0) > budget_left:
            continue
        if sodium_left is not None:
            na = _sodium_of(c, sodium_by_id)
            if na is None or na > sodium_left:
                continue
        valid.append(c)
    if not valid:
        return None, 0.0
    best = max(valid, key=lambda c: _score_candidate(
        c, meal_colors, weights,
        sodium_by_id=sodium_by_id, sugar_by_id=sugar_by_id, commercial_menu_ids=commercial_menu_ids))
    return best, _score_candidate(best, meal_colors, weights,
                                  sodium_by_id=sodium_by_id, sugar_by_id=sugar_by_id,
                                  commercial_menu_ids=commercial_menu_ids)


# ===========================================================================
# 핵심 진입점
# ===========================================================================
def derive_alternative_menus(plan, menus, allergy_groups, *,
                             hard_config=None, weights=None,
                             sodium_by_id=None, sugar_by_id=None, commercial_menu_ids=None):
    """공통식 plan 에서 알레르기 그룹별 대체식을 파생한다(하드 필터 + 소프트 점수).

    Args:
        plan: 공통식 plan {day: {meal: [menu_name, ...]}} (build_and_solve 결과의 .plan).
        menus: MenuItem 리스트(공통식과 동일 후보 풀).
        allergy_groups: list[AllergyGroup].
        hard_config: 공통식에 쓴 HardConstraintConfig(칼로리 밴드·예산·나트륨 상한 필터에 사용).
            None이면 하드 필터 생략. nutrient_max_per_day['sodium'] 이 있으면 교체 후에도
            그날 총 나트륨이 상한 이내인 후보만 고른다 — 이때 sodium_by_id 주입이 사실상 필수다
            (나트륨을 모르는 후보는 안전을 위해 탈락 → 전부 unresolved 가 될 수 있음).
        weights: AltScoreWeights. None이면 기본값.
        sodium_by_id/sugar_by_id: {menu_id: 값} 주입(소프트 감점). commercial_menu_ids: 완제품 menu_id 집합.

    Returns:
        list[AlternativeMenu].
    """
    w = weights or AltScoreWeights()
    by_name = {}
    for m in menus:
        by_name.setdefault(getattr(m, "name", None), m)

    # 하드 밴드/예산/나트륨 상한
    band = None
    day_budget = None
    sodium_cap = None
    if hard_config is not None:
        sodium_cap = (getattr(hard_config, "nutrient_max_per_day", None) or {}).get("sodium")
        if getattr(hard_config, "enable_energy", True):
            t = hard_config.target_kcal_per_day
            tol = hard_config.kcal_tolerance
            band = (t * (1 - tol), t * (1 + tol))
        if getattr(hard_config, "budget_limit_per_person", None) is not None \
                and getattr(hard_config, "budget_period", "day") == "day":
            day_budget = hard_config.budget_limit_per_person

    results = []
    for grp in allergy_groups:
        alt_plan, subs, unresolved = {}, [], []
        for day, meals in plan.items():
            alt_plan[day] = {}
            # 하루 running 총 칼로리/원가(스왑마다 갱신)
            day_kcal = sum((getattr(by_name.get(n), "calories", 0.0) or 0.0)
                           for ms in meals.values() for n in ms)
            day_cost = sum((getattr(by_name.get(n), "cost_won", 0.0) or 0.0)
                           for ms in meals.values() for n in ms)
            day_sodium = None
            if sodium_cap is not None:
                day_sodium = sum((_sodium_of(by_name.get(n), sodium_by_id) or 0.0)
                                 for ms in meals.values() for n in ms)
            for meal, picks in meals.items():
                new_picks = list(picks)
                current = set(picks)
                for i, name in enumerate(picks):
                    mi = by_name.get(name)
                    hit = set(getattr(mi, "allergens", set()) or set()) & grp.allergens if mi else set()
                    if not (mi and hit):
                        continue
                    # 이 끼니의 색(교체 대상 제외)
                    meal_colors = set()
                    for other in new_picks:
                        if other == name:
                            continue
                        om = by_name.get(other)
                        meal_colors |= set(getattr(om, "colors", set()) or set())
                    ocal = getattr(mi, "calories", 0.0) or 0.0
                    ocost = getattr(mi, "cost_won", 0.0) or 0.0
                    budget_left = (day_budget - (day_cost - ocost)) if day_budget is not None else None
                    ona = _sodium_of(mi, sodium_by_id) or 0.0
                    sodium_left = (None if day_sodium is None
                                   else sodium_cap - (day_sodium - ona))
                    alt, sc = _pick_alternative(
                        mi, menus, grp.allergens,
                        exclude_names=current, meal_colors=meal_colors,
                        day_kcal_wo_orig=day_kcal - ocal, band=band, budget_left=budget_left,
                        weights=w, sodium_by_id=sodium_by_id, sugar_by_id=sugar_by_id,
                        commercial_menu_ids=commercial_menu_ids, sodium_left=sodium_left)
                    if alt is None:
                        unresolved.append(Substitution(day, meal, mi.category, name, None, hit))
                        continue
                    # 커밋: 접시 교체 + running 총량 갱신
                    subs.append(Substitution(day, meal, mi.category, name, alt.name, hit, round(sc, 2)))
                    new_picks[i] = alt.name
                    current.discard(name); current.add(alt.name)
                    day_kcal += (getattr(alt, "calories", 0.0) or 0.0) - ocal
                    day_cost += (getattr(alt, "cost_won", 0.0) or 0.0) - ocost
                    if day_sodium is not None:
                        day_sodium += (_sodium_of(alt, sodium_by_id) or 0.0) - ona
                alt_plan[day][meal] = new_picks

        daily_kcal, total_cost = _recompute(alt_plan, by_name)
        results.append(AlternativeMenu(grp, alt_plan, subs, unresolved, daily_kcal, total_cost))
    return results


def _recompute(plan, by_name):
    """대체식 plan 의 일별 kcal·총원가를 재집계한다.

    접시별 int() 절단을 하지 않는다 — 공통식(csp_solver)·Hard 리포트와 같은 기준으로
    집계해야 두 트랙의 수치를 나란히 놓고 비교할 수 있다(2026-08-10 절단 버그 수정).
    """
    daily_kcal, total_cost = {}, 0.0
    for day, meals in plan.items():
        day_c = 0.0
        for _, picks in meals.items():
            for name in picks:
                m = by_name.get(name)
                if m:
                    day_c += getattr(m, "calories", 0.0) or 0.0
                    total_cost += getattr(m, "cost_won", 0.0) or 0.0
        daily_kcal[day] = round(day_c, 1)
    return daily_kcal, round(total_cost)


# ===========================================================================
# 출력(리포팅)
# ===========================================================================
def print_alternatives(alts):
    for alt in alts:
        g = alt.group
        print("=" * 60)
        print(f"[대체식] {g.label}  (배제: {', '.join(sorted(g.allergens))}"
              f"{f' · {g.count}명' if g.count else ''})")
        print("=" * 60)
        if not alt.substitutions and not alt.unresolved:
            print("  교체 없음 — 공통식에 해당 알레르겐 메뉴가 없음.")
        for s in alt.substitutions:
            print(f"  · {s.day}일 {s.meal} [{s.category}] {s.original} → {s.alternative}"
                  f"  (사유: {', '.join(sorted(s.hit_allergens))} · 점수 {s.score})")
        for s in alt.unresolved:
            print(f"  ⚠ {s.day}일 {s.meal} [{s.category}] {s.original} → 하드 지키는 대체 없음 · 영양사 확인"
                  f"  (사유: {', '.join(sorted(s.hit_allergens))})")
        print(f"  대체식 일별 kcal: {alt.daily_kcal} · 1인 총원가(참고): {alt.total_cost:,}원")