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
    # ── R90 알레르기 대체식 트랙 확장 ─────────────────────────────
    w_share: float = 10.0        # ★재료 공유 가점: 원래 접시와 재료가 겹칠수록 가산
                                 #   (간트 R90 "대체식이 기본식과 재료를 공유할수록 좋음").
                                 #   가장 큰 가중치 — 발주 품목·조리 공정·원가를 좌우하는 핵심 목적.
    w_popularity: float = 1.5    # 대중성 가점: '안전 자산' 대중 식재료(쌀·닭 등)일수록 가산
                                 #   (공유 식단 범용성 — 일반인도 거부감 없이 함께 먹을 수 있게).


# 완제품/튀김 판별 키워드(권위 신호는 ADR-004 COMMERCIAL / commercial_menu_ids 주입).
_COMMERCIAL_KEYWORDS = (
    "햄", "소시지", "비엔나", "맛살", "게맛살", "어묵", "오뎅", "베이컨", "스팸", "런천",
    "만두", "핫도그", "너겟", "너깃", "크로켓", "고로케", "돈가스", "돈까스", "카츠",
    "튀김", "까스", "탕수", "강정", "프라이", "후라이",
)


# ===========================================================================
# R90 알레르기 대체식 트랙 — 교차반응 그룹 · 대중 식재료 · 재료 공유
# ===========================================================================
# 교차반응 그룹(면역학적으로 함께 반응할 확률이 높은 알레르겐 묶음).
#   출처: 대전 아토피·천식 교육정보센터(우유↔양·염소젖 92%, 새우↔게 75% 등),
#         식약처·중앙급식관리지원센터 대체식품 자료의 교차반응 주의사항.
#   ★안전 원칙: 그룹의 배제 알레르겐이 한 묶음에 걸리면, 그 묶음 전체를 대체 후보에서 배제한다
#     (급식 알레르기 오류율 0% 목표 — 미확실은 배제 방향, csp_hard H-3 과 동일 위상).
#   ※ 근거 데이터가 확정되면 이 상수를 DB/설정으로 이관(현재는 명시적·결정론적 코드 상수).
CROSS_REACTIVE_GROUPS = (
    frozenset({"새우", "게", "랍스터", "바닷가재", "가재", "크릴"}),        # 갑각류
    frozenset({"오징어", "낙지", "문어", "굴", "전복", "홍합", "조개", "조개류"}),  # 연체·패류
    frozenset({"호두", "잣", "아몬드", "캐슈넛", "피스타치오", "땅콩"}),     # 견과·땅콩(임상 교차 빈번)
    frozenset({"복숭아", "사과", "배", "자두", "살구", "체리", "매실"}),     # 장미과 과일
    frozenset({"우유", "산양유", "염소유", "양유"}),                        # 유즙
    frozenset({"쇠고기", "소고기", "돼지고기"}),                            # 붉은 고기
    frozenset({"닭고기", "오리고기", "칠면조", "난류", "계란", "달걀"}),     # 가금·난류
    frozenset({"고등어", "삼치", "꽁치", "정어리"}),                        # 등푸른 생선
)

# '안전 자산' 대중 식재료 — 알레르기 유발 빈도가 낮고 대중적이라 공유 식단에 적합.
#   대중성 가점(w_popularity)에 사용. 확정 목록은 팀 협의로 조정 가능.
POPULAR_STAPLES = frozenset({
    "쌀", "쌀밥", "감자", "고구마", "닭고기", "두부", "무", "당근",
    "양파", "애호박", "배추", "김", "미역", "콩나물", "시금치",
})


def _ingredients_of(menu):
    """메뉴의 전체 재료명 집합. MenuItem.ingredients(추가 필드) 우선, 없으면 빈 집합.
    ※ ingredients 가 아직 안 실린 메뉴는 재료 공유 점수가 0 이 되어 기존 동작과 동일(안전)."""
    return set(getattr(menu, "ingredients", None) or set())


def _share_ratio(cand_ings, orig_ings):
    """대체 후보와 '교체 대상 원래 접시' 사이 재료 공유율(Jaccard, 0~1). 클수록 많이 겹침."""
    if not cand_ings or not orig_ings:
        return 0.0
    inter = cand_ings & orig_ings
    union = cand_ings | orig_ings
    return len(inter) / len(union) if union else 0.0


def _expand_cross_reactive(allergens):
    """배제 알레르겐 집합을 교차반응 그룹으로 확장한 '실제 배제 대상' 집합을 만든다.
    예: {'새우'} → {'새우','게','랍스터',...}(갑각류 전체). 걸리는 그룹이 없으면 원본 그대로."""
    unsafe = set(allergens)
    for group in CROSS_REACTIVE_GROUPS:
        if unsafe & group:
            unsafe |= group
    return unsafe


# 알레르겐 재료 → 대체 재료 후보(우선순위 순). 재료 치환(방식2)의 시드.
#   출처: 식약처·중앙급식관리지원센터 「알레르기 유발식품 대체식품」(공식 5종) +
#         "비슷한 영양소 식품으로 대체" 원칙. 그 자체가 고위험 알레르겐인 후보는 사전 제외했고,
#         남은 후보도 실제 사용 시 그룹 알레르겐·교차반응으로 한 번 더 필터한다(이중 안전).
#   ※ 키는 정규화된 재료명 기준(ingredient_synonym). 흔한 이형 표기는 별칭으로 함께 등록.
#   ※ 5종만 시드 — 미등록 알레르겐은 자동으로 방식1(메뉴 교체)로 폴백된다. 확장은 여기 줄만 추가.
SUBSTITUTE_MAP = {
    "우유": ("두유", "멸치", "김", "미역"),        # 칼슘·단백질 보전
    "난류": ("두부", "콩나물"),                    # 계란
    "계란": ("두부", "콩나물"),
    "달걀": ("두부", "콩나물"),
    "대두": ("김", "미역", "멸치"),                # 콩
    "콩":   ("김", "미역", "멸치"),
    "돼지고기": ("닭고기", "흰살생선"),            # 붉은고기 교차 피해 닭·생선 우선
    "밀":   ("쌀", "감자", "전분"),                # 곡류·전분
    "밀가루": ("쌀가루", "감자", "전분"),
}


@dataclass
class _SubstitutedMenu:
    """재료 치환으로 파생한 '가상 대체 접시'(같은 메뉴, 알레르겐 재료만 교체).
    MenuItem 을 덕타이핑 — derive/재집계가 읽는 속성만 갖춘다. 칼로리·원가는 원본 근사
    (치환은 소량 변경이라 밴드/예산 영향 미미 — 정밀 영양 델타는 향후 정제)."""
    menu_id: int
    name: str
    category: str
    calories: float
    cost_won: float
    colors: set
    allergens: set
    season_score: float
    ingredients: set


def _try_ingredient_substitution(orig, unsafe, *, substitute_map=None):
    """방식2: orig 의 알레르겐 재료만 안전 재료로 바꾼 '가상 대체 접시'를 만든다.

    같은 메뉴를 유지하므로 재료 공유율이 최대(알레르겐 자리만 바뀜)다 — 간트 R90 목적의 극한.
    성공 조건: 접시 안 '모든' 위험 재료가 (map 에 있고) 그룹에 안전한 대체를 가질 때.
    하나라도 대체 불가면 None → 호출부는 방식1(메뉴 교체)로 폴백.
    """
    smap = substitute_map or SUBSTITUTE_MAP
    orig_ings = _ingredients_of(orig)
    hits = orig_ings & unsafe                      # 이 접시에서 위험한 재료들
    if not hits:
        return None                                # 바꿀 게 없음(정상 접시)
    chosen = {}
    for h in hits:
        cands = smap.get(h)
        if not cands:
            return None                            # 이 알레르겐 재료엔 등록된 치환 없음 → 폴백
        pick = next((c for c in cands if c not in unsafe), None)
        if pick is None:
            return None                            # 후보가 전부 이 그룹엔 위험 → 폴백
        chosen[h] = pick
    new_ings = (orig_ings - set(hits)) | set(chosen.values())
    if new_ings & unsafe:                          # 치환 결과 재점검(이중 안전)
        return None
    swap_txt = ", ".join(f"{h}→{chosen[h]}" for h in sorted(hits))
    virt = _SubstitutedMenu(
        menu_id=getattr(orig, "menu_id", None),
        name=f"{getattr(orig, 'name', '')}(대체: {swap_txt})",
        category=getattr(orig, "category", None),
        calories=getattr(orig, "calories", 0.0) or 0.0,
        cost_won=getattr(orig, "cost_won", 0.0) or 0.0,
        colors=set(getattr(orig, "colors", set()) or set()),
        allergens=set(getattr(orig, "allergens", set()) or set()) - hits,
        season_score=getattr(orig, "season_score", 0.0) or 0.0,
        ingredients=new_ings,
    )
    return virt


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
    method: str = ""             # 대체 방식: '치환'(재료만 교체) / '메뉴대체'(다른 메뉴) / ''(미해결)


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


def _score_candidate(c, meal_colors, w, *, sodium_by_id, sugar_by_id, commercial_menu_ids,
                     orig_ingredients=frozenset(), popular_staples=POPULAR_STAPLES):
    """대체 후보 c 의 소프트 점수(클수록 좋음).

    R90 확장 두 항(기본식 소프트 규칙 위에 얹는 대체식 전용 목적):
      · 재료 공유 가점  : 교체 대상 원래 접시와 재료가 겹칠수록 가산(w_share). 핵심 목적.
      · 대중성 가점      : 안전 자산 대중 식재료를 포함할수록 가산(w_popularity).
    orig_ingredients 가 비면 공유 항은 0 → 기존 호출부 동작 불변(안전).
    """
    colors = set(getattr(c, "colors", set()) or set())
    new_colors = colors - meal_colors
    cid = getattr(c, "menu_id", None)
    sodium = (sodium_by_id or {}).get(cid, 0.0) or 0.0
    sugar = (sugar_by_id or {}).get(cid, 0.0) or 0.0
    cand_ings = _ingredients_of(c)
    score = 0.0
    score += w.w_season * (getattr(c, "season_score", 0.0) or 0.0)
    score += w.w_color * len(new_colors)
    score += w.w_share * _share_ratio(cand_ings, orig_ingredients)          # ★재료 공유
    score += w.w_popularity * len(cand_ings & (popular_staples or set()))   # 대중성
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
                      sodium_left=None, cross_reactive=True, popular_staples=POPULAR_STAPLES):
    """orig 를 대체할 최고점 안전 메뉴를 고른다. 하드 지키는 후보가 없으면 None.

    하드 필터: 같은 카테고리 · (교차반응 포함) 알레르겐 없음 · 끼니 내 중복 아님
             · (밴드 있으면) 교체 후 그날 총 칼로리가 밴드 안 · (예산 있으면) 잔여 예산 이내
             · (나트륨 상한 있으면) 교체 후 그날 총 나트륨이 상한 이내 [H-2e]
    소프트 점수: _score_candidate 최댓값 선정(★재료 공유·대중성 포함).

    ※ cross_reactive=True 면 배제 알레르겐을 교차반응 그룹으로 확장해 필터한다
      (예: 새우 알레르기 → 게·랍스터 등 갑각류 전체 배제). 안전을 위한 기본값.
    ※ sodium_left 가 주어졌는데 후보의 나트륨을 모르면 그 후보는 **탈락**시킨다
      (csp_hard_constraints H-2e 의 결측=배제 정책과 동일 방향).
    """
    unsafe = _expand_cross_reactive(allergens) if cross_reactive else set(allergens)
    orig_ings = _ingredients_of(orig)
    ocat = getattr(orig, "category", None)
    cands = [m for m in menus
             if getattr(m, "category", None) == ocat
             and not (set(getattr(m, "allergens", set()) or set()) & unsafe)
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

    def _sc(cand):
        return _score_candidate(
            cand, meal_colors, weights,
            sodium_by_id=sodium_by_id, sugar_by_id=sugar_by_id, commercial_menu_ids=commercial_menu_ids,
            orig_ingredients=orig_ings, popular_staples=popular_staples)

    best = max(valid, key=_sc)
    return best, _sc(best)


# ===========================================================================
# 핵심 진입점
# ===========================================================================
def derive_alternative_menus(plan, menus, allergy_groups, *,
                             hard_config=None, weights=None,
                             sodium_by_id=None, sugar_by_id=None, commercial_menu_ids=None,
                             cross_reactive=True, popular_staples=POPULAR_STAPLES,
                             ingredient_substitution=True, substitute_map=None):
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
                    # ── 1순위: 재료 치환(방식2) — 같은 메뉴 유지, 알레르겐 재료만 안전 재료로 ──
                    #   공유율 최대. 성공하면 그대로 채택(가상 접시를 by_name 에 등록해 재집계 반영).
                    alt, sc, via = None, 0.0, ""
                    unsafe = _expand_cross_reactive(grp.allergens) if cross_reactive else set(grp.allergens)
                    if ingredient_substitution:
                        sub = _try_ingredient_substitution(mi, unsafe, substitute_map=substitute_map)
                        if sub is not None:
                            alt = sub
                            by_name.setdefault(alt.name, alt)
                            sc = _score_candidate(
                                alt, meal_colors, w,
                                sodium_by_id=sodium_by_id, sugar_by_id=sugar_by_id,
                                commercial_menu_ids=commercial_menu_ids,
                                orig_ingredients=_ingredients_of(mi), popular_staples=popular_staples)
                            via = "치환"
                    # ── 2순위: 메뉴 교체(방식1) 폴백 — 치환 불가 시 다른 안전 메뉴로 ──
                    if alt is None:
                        alt, sc = _pick_alternative(
                            mi, menus, grp.allergens,
                            exclude_names=current, meal_colors=meal_colors,
                            day_kcal_wo_orig=day_kcal - ocal, band=band, budget_left=budget_left,
                            weights=w, sodium_by_id=sodium_by_id, sugar_by_id=sugar_by_id,
                            commercial_menu_ids=commercial_menu_ids, sodium_left=sodium_left,
                            cross_reactive=cross_reactive, popular_staples=popular_staples)
                        via = "메뉴대체"
                    if alt is None:
                        unresolved.append(Substitution(day, meal, mi.category, name, None, hit))
                        continue
                    # 커밋: 접시 교체 + running 총량 갱신
                    subs.append(Substitution(day, meal, mi.category, name, alt.name, hit, round(sc, 2), via))
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
            tag = f"[{s.method}] " if s.method else ""
            print(f"  · {s.day}일 {s.meal} [{s.category}] {tag}{s.original} → {s.alternative}"
                  f"  (사유: {', '.join(sorted(s.hit_allergens))} · 점수 {s.score})")
        for s in alt.unresolved:
            print(f"  ⚠ {s.day}일 {s.meal} [{s.category}] {s.original} → 하드 지키는 대체 없음 · 영양사 확인"
                  f"  (사유: {', '.join(sorted(s.hit_allergens))})")
        print(f"  대체식 일별 kcal: {alt.daily_kcal} · 1인 총원가(참고): {alt.total_cost:,}원")