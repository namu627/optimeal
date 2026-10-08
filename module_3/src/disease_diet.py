# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · 기저질환 대체식 (FR-11 '기저질환 상한' · '공통식 + 대체식' 트랙)

무엇을 하나(팀 결정 2026-10-06 — A안: 지금 알레르기 교체 로직 재활용, 재풀이 없음):
  · 공통식(일반식)은 그대로 둔다.
  · 질환 그룹마다, 그날 합계가 질환 기준을 넘으면 기여가 큰 접시부터 같은 카테고리의 낮은 메뉴로 교체한다.
    교체 후에도 열량 밴드·예산·(그룹의) 알레르기를 지킨다.
  · 알레르기도 있는 그룹: 질환 기준으로 먼저 맞추고, 그 식단에서 알레르기 메뉴만 alternative_menu 로 한 번 더
    교체한다 — 이때 질환 상한을 extra_caps 로 넘겨 바꾼 메뉴가 기준을 다시 넘지 않게 한다.
  · 기준을 못 맞춘 날은 가장 가까운 식단을 두고 '확인 필요'로 표시한다(영양사가 검토 화면에서 수정).

질환 기준(하루) — 출처는 응답(limits[].source)에도 싣는다:
  · 고혈압  나트륨 ≤ 2,000mg — 대한고혈압학회 진료지침(소금 5g).
  · 당뇨    탄수화물 열량 비율 ≤ 55% — 대한당뇨병학회 2023 진료지침(총에너지의 55~65%, 낮은 쪽 적용).
            비율은 접시 단위 선형식 4×탄수화물 − 0.55×열량 의 하루 합 ≤ 0 으로 바꿔 다른 상한과 같게 다룬다.
            당류는 기준 없이 합계만 보여 준다(info).
  · 신장질환 — 기준 확정 전(칼륨 mg 수치의 1차 근거 확인 중). KNOWN_DISEASES 에 없으면 '기준 미확정'으로
            응답만 하고 교체하지 않는다. 데이터(칼륨·인)는 ingredient_nutrient 로 이미 계산된다.

메뉴 값:
  · 나트륨·탄수화물·열량 = nutrition_recipe(식품안전나라 공식 1인분 값 — 솔버와 같은 값).
  · 당류·칼륨·인 = 재료 1인분 g × 100g당 값(ingredient_nutrient) 합을 '메뉴 공식 열량 ÷ 재료 합산 열량'으로 보정.
    레시피 인분 오류로 g 합이 부푼 메뉴가 많아(중앙값 0.5~0.8배) 솔버의 1인분 기준에 맞춘다.
    재료 g 의 80% 미만만 값이 있거나 보정비가 0.2~2.0 밖이면 '모름'(None) — 상한 판정에서 위험 쪽으로 다룬다
    (모르는 후보는 쓰지 않고, 식단에 있으면 먼저 바꾸며, 못 바꾸면 '확인 필요').
"""
from __future__ import annotations

from dataclasses import dataclass, field

import alternative_menu as am


# ===========================================================================
# 질환 기준
# ===========================================================================
@dataclass(frozen=True)
class NutrientLimit:
    key: str        # 값 키(sodium / carb_ratio / potassium / phosphorus)
    label: str
    cap: float      # 하루 상한(carb_ratio 는 %)
    unit: str
    source: str


SODIUM_HTN = NutrientLimit("sodium", "나트륨", 2000.0, "mg",
                           "대한고혈압학회 고혈압 진료지침 — 하루 소금 5g(나트륨 2,000mg) 이하")
CARB_RATIO_DM = NutrientLimit("carb_ratio", "탄수화물 열량 비율", 55.0, "%",
                              "대한당뇨병학회 2023 당뇨병 진료지침 — 탄수화물 총에너지의 55~65%(55% 적용)")

DISEASE_LIMITS: dict[str, tuple[NutrientLimit, ...]] = {
    "고혈압": (SODIUM_HTN,),
    "당뇨": (CARB_RATIO_DM,),
}
DISEASE_INFO: dict[str, tuple[str, ...]] = {"당뇨": ("sugar",)}   # 기준 없이 합계만 보여 줄 값
DISEASE_ALIASES = {"당뇨병": "당뇨", "신부전": "신장질환", "만성콩팥병": "신장질환", "콩팥병": "신장질환"}
KNOWN_DISEASES = ("고혈압", "당뇨", "신장질환")   # 화면 선택지. 이 중 DISEASE_LIMITS 에 없는 건 기준 미확정

UNMET = "질환 기준 미충족"
UNKNOWN_VALUE = "영양 정보 없음 — 질환 기준 판정 불가"

COVERAGE_MIN = 0.8                # 재료 g 중 값이 있는 비율 하한
SCALE_RANGE = (0.2, 2.0)          # 공식 열량 ÷ 재료 합산 열량 허용 범위(밖이면 매핑·레시피 오류로 보고 모름)
FRYING_MEDIUM = ("튀김기름",)       # 튀김용 기름 — 흡수량이 일부라 열량 합산에서 뺀다(칼륨·인·당류는 0)


def normalize_diseases(values) -> set:
    out = set()
    for v in values or ():
        v = str(v).strip()
        if v:
            out.add(DISEASE_ALIASES.get(v, v))
    return out


def limits_for(diseases) -> list[NutrientLimit]:
    """그룹 질환들의 상한 목록(같은 키는 더 엄격한 값 하나)."""
    best: dict[str, NutrientLimit] = {}
    for d in sorted(diseases):
        for lim in DISEASE_LIMITS.get(d, ()):
            cur = best.get(lim.key)
            if cur is None or lim.cap < cur.cap:
                best[lim.key] = lim
    return list(best.values())


def pending_diseases(diseases) -> list[str]:
    """기준이 아직 없는 질환(신장질환 등) — 교체하지 않고 응답에 '기준 미확정'으로 남긴다."""
    return sorted(d for d in diseases if d not in DISEASE_LIMITS)


# ===========================================================================
# 메뉴 값 조회
# ===========================================================================
_VALUES_SQL = """
WITH ing AS (
    SELECT r.nutrition_recipe_id AS menu_id,
           SUM(rim.per_serving_grams) AS g,
           SUM(rim.per_serving_grams) FILTER (WHERE n.energy_kcal IS NOT NULL)   AS g_e,
           SUM(rim.per_serving_grams * n.energy_kcal / 100)                      AS e,
           SUM(rim.per_serving_grams) FILTER (WHERE n.sugar_g IS NOT NULL)       AS g_s,
           SUM(rim.per_serving_grams * n.sugar_g / 100)                          AS s,
           SUM(rim.per_serving_grams) FILTER (WHERE n.potassium_mg IS NOT NULL)  AS g_k,
           SUM(rim.per_serving_grams * n.potassium_mg / 100)                     AS k,
           SUM(rim.per_serving_grams) FILTER (WHERE n.phosphorus_mg IS NOT NULL) AS g_p,
           SUM(rim.per_serving_grams * n.phosphorus_mg / 100)                    AS p
    FROM recipe r
    JOIN recipe_ingredient_map rim ON rim.recipe_id = r.recipe_id AND rim.per_serving_grams > 0
    JOIN ingredient i               ON i.ingredient_id = rim.ingredient_id
    LEFT JOIN ingredient_nutrient n ON n.ingredient_id = rim.ingredient_id
    WHERE r.nutrition_recipe_id = ANY(:ids) AND i.ingredient_name <> ALL(:frying)
    GROUP BY r.nutrition_recipe_id
)
SELECT nr.nutrition_id AS menu_id, nr.calories, nr.sodium, nr.carbs,
       ing.g, ing.g_e, ing.e, ing.g_s, ing.s, ing.g_k, ing.k, ing.g_p, ing.p
FROM nutrition_recipe nr
LEFT JOIN ing ON ing.menu_id = nr.nutrition_id
WHERE nr.nutrition_id = ANY(:ids)
"""


def estimate_from_ingredients(row: dict) -> dict:
    """재료 합산 행 → {sugar, potassium, phosphorus} 1인분 추정(모르면 None). 순수 함수(테스트용)."""
    out = {"sugar": None, "potassium": None, "phosphorus": None}
    row = {k: (float(v) if v is not None and not isinstance(v, str) else v) for k, v in row.items()}  # DB Decimal
    g, g_e, e, kcal = row.get("g"), row.get("g_e"), row.get("e"), row.get("calories")
    if not g or not g_e or not e or not kcal or g_e < COVERAGE_MIN * g:
        return out
    ingredient_kcal = float(e) / (float(g_e) / float(g))      # 값 없는 재료 g 만큼 같은 밀도로 외삽
    scale = float(kcal) / ingredient_kcal if ingredient_kcal > 0 else 0.0
    if not (SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]):
        return out
    for key, gk, vk in (("sugar", "g_s", "s"), ("potassium", "g_k", "k"), ("phosphorus", "g_p", "p")):
        gv, v = row.get(gk), row.get(vk)
        if gv and v is not None and gv >= COVERAGE_MIN * g:
            out[key] = round(scale * float(v) / (float(gv) / float(g)), 1)
    return out


_BASIC_SQL = """
SELECT nutrition_id AS menu_id, calories, sodium, carbs
FROM nutrition_recipe WHERE nutrition_id = ANY(:ids)
"""


def load_menu_values(engine, menus) -> tuple[dict[int, dict], bool]:
    """(menu_id → {kcal, sodium, carbs, sugar, potassium, phosphorus}, 재료 추정 사용 여부). 모르는 값은 None.

    ingredient_nutrient 가 없는 DB(migrations/v6·적재 전)에서도 고혈압·당뇨는 공식 값만으로 돌아가야 하므로,
    재료 조인이 실패하면 공식 값만 읽고 당류·칼륨·인은 None 으로 둔다(두 번째 반환값 False).
    """
    from sqlalchemy import text

    ids = [m.menu_id for m in menus if getattr(m, "menu_id", None) is not None]
    out: dict[int, dict] = {}
    if not ids:
        return out, True
    estimated = True
    try:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                text(_VALUES_SQL), {"ids": ids, "frying": list(FRYING_MEDIUM)}).mappings()]
    except Exception:
        estimated = False
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(text(_BASIC_SQL), {"ids": ids}).mappings()]
    for r in rows:
        out[r["menu_id"]] = {
            "kcal": float(r["calories"]) if r["calories"] is not None else None,
            "sodium": float(r["sodium"]) if r["sodium"] is not None else None,
            "carbs": float(r["carbs"]) if r["carbs"] is not None else None,
            **estimate_from_ingredients(r),
        }
    return out, estimated


def dish_load(limit: NutrientLimit, vals: dict | None) -> float | None:
    """접시 하나가 상한에 더하는 양. carb_ratio 는 4×탄수화물 − 비율×열량(하루 합 ≤ 0 이 기준 충족)."""
    if not vals:
        return None
    if limit.key == "carb_ratio":
        c, k = vals.get("carbs"), vals.get("kcal")
        if c is None or k is None:
            return None
        return 4.0 * c - limit.cap / 100.0 * k
    return vals.get(limit.key)


def day_cap(limit: NutrientLimit) -> float:
    return 0.0 if limit.key == "carb_ratio" else limit.cap


def allergy_caps(limits, values_by_id) -> dict:
    """alternative_menu.derive_alternative_menus(extra_caps=...) 용 {키: (하루 상한, {menu_id: 접시 값})}."""
    return {lim.key: (day_cap(lim), {mid: dish_load(lim, v) for mid, v in values_by_id.items()})
            for lim in limits}


# ===========================================================================
# 결과 자료구조
# ===========================================================================
@dataclass
class DietGroup(am.AllergyGroup):
    """알레르기 + 기저질환 + 인원. 질환이 없으면 알레르기 그룹과 같다."""
    diseases: set = field(default_factory=set)

    @property
    def kind(self) -> str:
        if self.allergens and self.diseases:
            return "알레르기+기저질환"
        return "기저질환" if self.diseases else "알레르기"


@dataclass
class DiseaseSwap:
    day: int
    meal: str
    category: str
    original: str
    alternative: str
    reduced: list            # 줄이려던 상한 키(sodium 등). 값 모름 교체면 ['unknown']
    score: float = 0.0
    method: str = "질환기준"


# ===========================================================================
# 질환 기준 교체(A안)
# ===========================================================================
def _excess(totals: dict, limits, scales: dict) -> float:
    """상한 초과량의 정규화 합(0 = 모두 충족)."""
    return sum(max(0.0, totals[l.key] - day_cap(l)) / scales[l.key] for l in limits)


def derive_disease_plan(plan, menus, limits, values_by_id, *, allergens=(), hard_config=None,
                        weights=None, commercial_menu_ids=None, cross_reactive=True):
    """plan 에서 질환 상한을 넘는 날의 접시를 같은 카테고리의 낮은 메뉴로 교체한다(탐욕, 재풀이 없음).

    Returns:
        (새 plan, list[DiseaseSwap]). 못 맞춘 날은 그대로(가장 가까운 상태) 두고 day_report 가 표시한다.
    """
    if not limits:
        return {d: {m: list(p) for m, p in ms.items()} for d, ms in plan.items()}, []
    w = weights or am.AltScoreWeights()
    by_name: dict = {}
    for m in menus:
        by_name.setdefault(m.name, m)
    unsafe = am._expand_cross_reactive(am.normalize_allergens(allergens)) if cross_reactive \
        else am.normalize_allergens(allergens)
    target = getattr(hard_config, "target_kcal_per_day", 2000.0) if hard_config else 2000.0
    # 정규화 척도 — 상한 대비 초과 비율(탄수화물 비율은 목표 열량 기준 탄수화물 열량)
    scales = {l.key: (l.cap / 100.0 * target if l.key == "carb_ratio" else l.cap) or 1.0 for l in limits}
    band, day_budget, total_budget = None, None, None
    if hard_config is not None:
        if getattr(hard_config, "enable_energy", True):
            tol = hard_config.kcal_tolerance
            band = (hard_config.target_kcal_per_day * (1 - tol), hard_config.target_kcal_per_day * (1 + tol))
        if getattr(hard_config, "budget_limit_per_person", None) is not None:
            if getattr(hard_config, "budget_period", "day") == "day":
                day_budget = hard_config.budget_limit_per_person
            else:
                total_budget = hard_config.budget_limit_per_person * len(plan)

    def load(name, lim):
        mi = by_name.get(name)
        return dish_load(lim, values_by_id.get(getattr(mi, "menu_id", None)))

    def cost(name):
        return getattr(by_name.get(name), "cost_won", 0.0) or 0.0

    def kcal(name):
        return getattr(by_name.get(name), "calories", 0.0) or 0.0

    period_cost = sum(cost(n) for ms in plan.values() for p in ms.values() for n in p)
    new_plan, swaps = {}, []
    by_cat: dict = {}
    for m in menus:
        by_cat.setdefault(m.category, []).append(m)

    def best_for_slot(picks, slots, totals, excess, meal, name, is_unknown):
        """slot(meal, name) 을 바꿀 최선 후보 → (정렬키, 후보, 점수) 또는 None."""
        mi = by_name.get(name)
        if mi is None:
            return None
        day_kcal = sum(kcal(n) for _, _, n in slots)
        day_cost = sum(cost(n) for _, _, n in slots)
        meal_names = set(picks[meal])
        meal_colors = set()
        for n in picks[meal]:
            if n != name:
                meal_colors |= set(getattr(by_name.get(n), "colors", set()) or set())
        best = None
        for c in by_cat.get(mi.category, ()):
            if c.name in meal_names or (unsafe and am.menu_has_unsafe(c, unsafe)):
                continue
            cl = {l.key: dish_load(l, values_by_id.get(c.menu_id)) for l in limits}
            if any(v is None for v in cl.values()):
                continue                              # 값 모르는 후보는 쓰지 않는다
            nk = day_kcal - kcal(name) + (c.calories or 0.0)
            if band is not None and not (band[0] <= nk <= band[1]):
                continue
            cc = getattr(c, "cost_won", 0.0) or 0.0
            if day_budget is not None and day_cost - cost(name) + cc > day_budget:
                continue
            if total_budget is not None and period_cost - cost(name) + cc > total_budget:
                continue
            nt = {l.key: totals[l.key] - (load(name, l) or 0.0) + cl[l.key] for l in limits}
            ne = _excess(nt, limits, scales)
            if not is_unknown and ne >= excess - 1e-9:
                continue                              # 초과를 줄이지 못하면 바꾸지 않는다
            if is_unknown and ne > excess + 1e-9:
                continue                              # 값 모름 교체는 (아는 값 기준) 더 나빠지지만 않으면
            sc = am._score_candidate(c, meal_colors, w, sodium_by_id=None, sugar_by_id=None,
                                     commercial_menu_ids=commercial_menu_ids,
                                     orig_ingredients=am._ingredients_of(mi))
            key = (round(ne, 4), -sc)
            if best is None or key < best[0]:
                best = (key, c, sc)
        return best

    for day, meals in plan.items():
        picks = {meal: list(p) for meal, p in meals.items()}
        tried: set = set()                            # 한 번 바꾼(또는 바꿀 수 없던) 자리 — 되바꾸기 방지
        while True:
            slots = [(meal, i, n) for meal, p in picks.items() for i, n in enumerate(p)]
            totals = {l.key: sum(load(n, l) or 0.0 for _, _, n in slots) for l in limits}
            unknown = {(meal, i) for meal, i, n in slots if any(load(n, l) is None for l in limits)}
            excess = _excess(totals, limits, scales)
            if excess <= 1e-9 and not unknown:
                break
            violated = [l for l in limits if totals[l.key] > day_cap(l) + 1e-9]
            # 값 모름 접시를 먼저 바꾼다(그날 합계를 판정할 수 없으므로). 그다음 초과한 상한에 기여하는 접시 중
            # '바꾼 뒤 초과가 가장 작아지는' 교체 하나 — 기여 큰 접시부터 바꾸면 한 번이면 될 걸 여러 번 바꾼다.
            pending_unknown = [s for s in slots if (s[0], s[1]) in unknown and (s[0], s[1]) not in tried]
            pending_over = [s for s in slots if (s[0], s[1]) not in unknown and (s[0], s[1]) not in tried
                            and any((load(s[2], l) or 0.0) > 0 for l in violated)]
            choice = None
            for pending in (pending_unknown, pending_over):
                for meal, i, name in pending:
                    found = best_for_slot(picks, slots, totals, excess, meal, name, (meal, i) in unknown)
                    if found is None:
                        tried.add((meal, i))          # 이 자리는 바꿀 후보가 없다
                    elif choice is None or found[0] < choice[0][0]:
                        choice = (found, meal, i, name)
                if choice is not None:
                    break
            if choice is None:
                break                                 # 더 바꿀 접시가 없음 → 이 상태로 두고 '확인 필요'
            (_, c, sc), meal, i, name = choice
            mi = by_name[name]
            picks[meal][i] = c.name
            tried.add((meal, i))
            period_cost += (getattr(c, "cost_won", 0.0) or 0.0) - cost(name)
            swaps.append(DiseaseSwap(day, meal, mi.category, name, c.name,
                                     ["unknown"] if (meal, i) in unknown else [l.key for l in violated],
                                     round(sc, 2)))
        new_plan[day] = picks
    return new_plan, swaps


def _base_menu(by_name, name):
    """메뉴명 → MenuItem. 재료 치환 접시 '메뉴(대체: 우유→두유)' 는 원래 메뉴로 본다 — 치환은 소량 변경이라
    alternative_menu 도 열량·원가를 원본 값으로 둔다(같은 근사)."""
    return by_name.get(name) or by_name.get(str(name).split("(대체:")[0].strip())


def day_report(plan, menus, limits, values_by_id, info_keys=()) -> dict:
    """{day: {status, limits: {키: {total, cap, ok, unit}}, info: {키: 합계}, unknown_menus: [...]}}.

    carb_ratio 의 total 은 실제 비율(%) 로 보여 준다(4×Σ탄수화물 ÷ Σ열량).
    """
    by_name: dict = {}
    for m in menus:
        by_name.setdefault(m.name, m)
    out = {}
    for day, meals in plan.items():
        names = [n for p in meals.values() for n in p]
        vals = [values_by_id.get(getattr(_base_menu(by_name, n), "menu_id", None)) or {} for n in names]
        rep, unknown = {}, set()
        for lim in limits:
            loads = [dish_load(lim, v) for v in vals]
            unknown |= {n for n, x in zip(names, loads) if x is None}
            if lim.key == "carb_ratio":
                carbs = sum(v.get("carbs") or 0.0 for v in vals)
                kcal = sum(v.get("kcal") or 0.0 for v in vals)
                total = round(400.0 * carbs / kcal, 1) if kcal else None
            else:
                total = round(sum(x or 0.0 for x in loads), 1)
            ok = total is not None and total <= lim.cap + 1e-9
            rep[lim.key] = {"label": lim.label, "total": total, "cap": lim.cap, "unit": lim.unit, "ok": ok}
        info = {}
        for key in info_keys:
            xs = [v.get(key) for v in vals]
            info[key] = {"total": round(sum(x for x in xs if x is not None), 1),
                         "unknown": sum(x is None for x in xs)}
        met = all(r["ok"] for r in rep.values()) and not unknown
        out[day] = {"status": "충족" if met else "확인 필요", "limits": rep, "info": info,
                    "unknown_menus": sorted(unknown)}
    return out


def derive_diet_alternatives(plan, menus, groups, values_by_id, *, hard_config=None,
                             sodium_by_id=None, commercial_menu_ids=None, weights=None):
    """그룹(알레르기·기저질환·둘 다)별 대체식.

    알레르기만 → alternative_menu 그대로. 질환 있음 → derive_disease_plan 다음 알레르기 교체(extra_caps).
    Returns: list[dict] — AlternativeMenu 필드(group·plan·substitutions·unresolved·daily_kcal·total_cost) +
             kind·diseases·pending_diseases·limits·disease_swaps·day_report·unmet_days.
    """
    results = []
    for grp in groups:
        diseases = normalize_diseases(getattr(grp, "diseases", ()))
        limits = limits_for(diseases)
        base, dswaps = plan, []
        if limits:
            base, dswaps = derive_disease_plan(
                plan, menus, limits, values_by_id, allergens=grp.allergens, hard_config=hard_config,
                weights=weights, commercial_menu_ids=commercial_menu_ids)
        caps = allergy_caps(limits, values_by_id) if limits else None
        alt = am.derive_alternative_menus(
            base, menus, [grp], hard_config=hard_config, sodium_by_id=sodium_by_id,
            commercial_menu_ids=commercial_menu_ids, extra_caps=caps)[0]
        info_keys = tuple(sorted({k for d in diseases for k in DISEASE_INFO.get(d, ())}))
        report = day_report(alt.plan, menus, limits, values_by_id, info_keys) if limits else {}
        results.append({
            "group": grp, "kind": grp.kind if isinstance(grp, DietGroup) else "알레르기",
            "diseases": sorted(diseases), "pending_diseases": pending_diseases(diseases),
            "plan": alt.plan, "substitutions": alt.substitutions, "unresolved": alt.unresolved,
            "daily_kcal": alt.daily_kcal, "total_cost": alt.total_cost,
            "limits": [{"key": l.key, "label": l.label, "cap": l.cap, "unit": l.unit, "source": l.source}
                       for l in limits],
            "disease_swaps": dswaps, "day_report": report,
            "unmet_days": sorted(d for d, r in report.items() if r["status"] != "충족"),
        })
    return results
