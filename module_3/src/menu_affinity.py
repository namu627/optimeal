# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · 메뉴 어울림 Soft 제약 (B6 Phase 2)

`soft_constraints.py`·`soft_constraints_diversity.py` 와 **동일 규약**의 Soft 모듈이다
(순수 함수, 모델 주입, 키워드 인자, side-channel 주입 + 우아한 저하, 결과 dataclass,
`evaluate_*` 리포팅 동반). `.score` 를 다른 Soft 항과 그냥 더해 Maximize 하면 된다.

점수의 출처 — 코드가 아니라 **데이터**:
    `data/processed/menu_affinity.csv` (B6 Phase 0, 2026-08-13 산출).
    NEIS 실 급식 15,250끼니의 동시출현 빈도에서 계산한 log(관측/기대)×100 이다.
    **LLM 확률 출력 금지**(CLAUDE.md 핵심 제약)라 점수는 관측 빈도에서만 나올 수 있고,
    파일로 두는 이유는 영양사 검수 결과로 통째로 덮어쓸 수 있어야 하기 때문이다.
    표가 없으면 항 전체가 비활성(우아한 저하) — 8/14 이전과 동일 동작.

이번 차수에 켜는 축 (2026-08-15 사용자 결정으로 범위가 좁혀졌다):
    ②③ 조리법 조합  — 한 끼니 안 같은 조리법 중복 회피. **이번 Phase 의 실질 내용**.
                       조림 0.40 · 무침 0.47 · 찜 0.51 · 구이 0.54 · 튀김 0.79 · 볶음 0.86
                       (전부 lift < 1). 우리 모델 어디에도 없던 신호다.
    ⑤ 주식유형×국유형 — 표는 읽되 **H-4c(Hard)가 유지되면 기여가 0 이다**(§왜 0인가).
    ① 주식유형×김치   — 김치 1개 고정이 유지되어 상수항이 되므로 **기본 OFF**(§왜 OFF인가).
    ④ 맛 계열        — Phase 0 에서 데이터 부족으로 제외 확정(커버리지 13.4%).

읽어야 할 것: [[design/menu_affinity]] §Phase 2, `scripts/build_menu_affinity.py`(표 생성).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

try:
    from . import menu_taxonomy as _mt
    from . import soft_constraints_diversity as _scd
except ImportError:  # `python menu_affinity.py` 직접 실행 지원 — 다른 모듈과 동일 규약
    import menu_taxonomy as _mt
    import soft_constraints_diversity as _scd


# 근거표 기본 경로 (module_3/src/ → 저장소 루트 → data/processed/).
DEFAULT_TABLE_PATH = (Path(__file__).resolve().parents[2]
                      / "data" / "processed" / "menu_affinity.csv")

# 근거표의 축 이름 (build_menu_affinity.py 가 쓰는 값과 일치해야 한다).
AXIS_STAPLE_SOUP = "주식유형×국유형"
AXIS_STAPLE_KIMCHI = "주식유형×김치"
AXIS_METHOD = "조리법×조리법"
AXIS_TASTE = "맛계열×맛계열"

# 조리법 축의 대상 카테고리. Phase 0 집계가 **주찬·부찬만** 반찬으로 보았으므로
# (`build_menu_affinity.meal_axes` 의 sides) 모델에서도 같은 범위여야 점수가 유효하다.
SIDE_CATEGORIES: tuple[str, ...] = (_mt.MAIN_SIDE, _mt.SUB_SIDE)

# 국 유형 중 '없음'(국 없는 끼니)은 우리 구성에서 불가능하다(composition 국=1).
NO_SOUP = "없음"
KIMCHI_PRESENT = "김치있음"


@dataclass
class AffinityRow:
    """근거표 한 행. 점수는 log(lift)×100 (양수=선호, 음수=기피)."""
    axis: str
    value_a: str
    value_b: str
    score: int
    n_both: int
    confidence: str          # 'ok' | 'low' (관측 30건 미만)
    # 관측/기대 비. 점수는 log(lift)×100 이라 사람에게 보일 때는 lift 가 읽기 쉽다
    # (검수 폼이 "실제로 같이 나오는 정도"로 쓴다). 1보다 작으면 피하는 경향.
    lift: float = 0.0


def load_affinity_table(path: str | Path | None = None) -> list[AffinityRow]:
    """어울림 근거표를 읽는다. 파일이 없으면 빈 목록(항 비활성 → 우아한 저하).

    Args:
        path: CSV 경로. None 이면 `DEFAULT_TABLE_PATH`.

    Returns:
        AffinityRow 목록. 읽기 실패(부재·형식 오류)는 예외 대신 빈 목록 —
        어울림은 Soft 라 없으면 중립일 뿐이고, 이 때문에 식단 생성이 죽으면 안 된다.
    """
    p = Path(path or DEFAULT_TABLE_PATH)
    if not p.exists():
        return []
    rows: list[AffinityRow] = []
    try:
        with open(p, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows.append(AffinityRow(
                    axis=r["axis"], value_a=r["value_a"], value_b=r["value_b"],
                    score=int(float(r["score"])), n_both=int(float(r["n_both"])),
                    confidence=r.get("confidence", "ok"),
                    lift=float(r.get("lift") or 0.0),
                ))
    except (OSError, KeyError, ValueError):
        return []
    return rows


@dataclass
class AffinityWeights:
    """어울림 항 가중치. 다른 Soft 모듈과 같은 "클수록 좋음" 규약(정수 계수)."""

    # 조리법 중복(같은 조리법 2접시 이상) — Phase 0 에서 가장 뚜렷했던 신호.
    w_method_same: int = 1
    # 조리법 교차 조합(구이+찜 등) — Phase 0 판정 "후순위". 주찬 1개 구조와 중복 소지가
    # 있고 NEIS 에는 주찬/부찬 구분이 없어 분리 측정이 불가하다 → 기본 OFF.
    w_method_cross: int = 0
    # 주식유형×국유형 — H-4c(Hard)가 켜져 있으면 남는 자유도가 rice 뿐이라 기여 0.
    w_staple_soup: int = 1
    # 주식유형×김치 — 김치 1개 고정(8/12 영양사 확정)이 유지되는 한 상수항이라 기본 OFF.
    w_staple_kimchi: int = 0

    # 점수 척도 정렬: 표의 점수는 log(lift)×100(±300)이라 그대로 쓰면 다른 Soft 항
    # (w_cook=8 등)을 압도한다. 10으로 나눠 "조림 중복 1회 ≈ 조리법 종수 1종 부족" 수준에
    # 맞춘다(조림 -92 → -9, 무침 -76 → -8 vs w_cook 8).
    score_divisor: int = 10
    # 관측 30건 미만(confidence='low') 행 사용 여부. 기본 미사용 —
    # 희소 축(porridge n_both=3 등)에 강한 점수를 주면 표본 잡음을 규칙으로 굳힌다.
    use_low_confidence: bool = False


@dataclass
class AffinitySoftObjective:
    """add_affinity_soft_objective 결과. `.score` 를 다른 Soft 항과 더해 Maximize."""
    score: object                                     # cp_model LinearExpr
    active_terms: dict = field(default_factory=dict)  # {축: 활성여부}
    method_dup_vars: dict = field(default_factory=dict)   # {(d,s,조리법): BoolVar}
    method_cross_vars: dict = field(default_factory=dict)  # {(d,s,a,b): BoolVar}
    staple_soup_vars: dict = field(default_factory=dict)  # {(d,s,주식유형,국유형): BoolVar}
    # 제약이 실제로 쓴 라벨·계수. 리포트는 **이 값만** 읽는다 — MenuItem 을 다시
    # 분류하면 언젠가 두 경로가 갈려 표가 조용히 거짓말을 한다(2026-08-11·08-14 전례).
    method_by_idx: dict = field(default_factory=dict)     # {메뉴인덱스: 조리법}
    coefficients: dict = field(default_factory=dict)      # {(축, a, b): 정수계수}


# ===========================================================================
# (A) 표 → 계수 변환
# ===========================================================================
def _usable(rows: list[AffinityRow], axis: str, w: AffinityWeights) -> list[AffinityRow]:
    """축에 해당하고 신뢰도 기준을 통과한 행만 남긴다."""
    return [r for r in rows if r.axis == axis
            and (w.use_low_confidence or r.confidence == "ok")]


def _relative_staple_soup(rows: list[AffinityRow]) -> dict:
    """⑤ 축 점수를 **주식유형별 상대값**으로 바꾼다(모두 ≤ 0).

    왜 상대화하는가: 표의 기준선에는 '국 없는 끼니'가 섞여 있다. NEIS 에서 면 끼니의
    80%가 국을 아예 내지 않으므로 noodle+soup 원점수가 -108 이 되는데, 이건 "면에
    국이 안 어울린다"가 아니라 **"면 끼니는 국을 잘 안 낸다"** 는 뜻이다. 우리 구성은
    국 1개가 필수라 그 상수분을 그대로 쓰면 어울림 항이 엉뚱하게 **면·빵 주식 자체를
    억제**한다. 주식유형 안에서 최대값을 0 으로 두면 상수분이 상쇄되고, 남는 것은
    "이 주식이면 어느 국이 맞는가"뿐이다.

    Returns:
        {(주식유형, 국유형): 상대점수(≤0)}. '없음' 행은 제외(구성상 불가능).
    """
    by_staple: dict = {}
    for r in rows:
        if r.value_b == NO_SOUP:
            continue
        by_staple.setdefault(r.value_a, {})[r.value_b] = r.score
    out: dict = {}
    for staple, scores in by_staple.items():
        best = max(scores.values())
        for soup, s in scores.items():
            out[staple, soup] = s - best
    return out


def _coef(score: int, weight: int, divisor: int) -> int:
    """표 점수를 목적함수 정수 계수로 바꾼다(0이면 항을 만들지 않는다)."""
    if not weight or not divisor:
        return 0
    return int(round(score / divisor)) * weight


# ===========================================================================
# (B) 목적함수 조립 — 핵심 진입점
# ===========================================================================
def add_affinity_soft_objective(
    model,
    x: dict,
    menus: list,
    *,
    days: int,
    n_meals: int,
    table: list[AffinityRow] | None = None,
    weights: AffinityWeights | None = None,
    cooking_methods: dict[int, str] | None = None,
    staple_kind_by_idx: dict[int, str] | None = None,
    soup_kind_by_idx: dict[int, str] | None = None,
    staple_category: str = "주식",
    soup_category: str = "국",
) -> AffinitySoftObjective:
    """메뉴 어울림 Soft 목적을 모델에 더하고 점수식을 돌려준다.

    모든 항이 **끼니 국소**다(하루·지평을 가로지르지 않는다). 덕분에 롤링 웜스타트의
    하루 부분 문제에 같은 함수를 그대로 걸 수 있고, "힌트가 모르는 제약은 SLA로
    되돌아온다"(2026-08-14 Phase 1)를 처음부터 피한다.

    Args:
        model: cp_model.CpModel.
        x: 결정변수 {(m,d,s): BoolVar} (골조와 동일 키).
        menus: MenuItem 리스트.
        days: 급식 일수. n_meals: 끼니 수.
        table: `load_affinity_table()` 결과. None/빈 목록이면 전 항 비활성.
        weights: AffinityWeights. None 이면 기본값.
        cooking_methods: {인덱스: 조리법} 부분 주입(DB 권위값). 없으면 메뉴명 키워드.
        staple_kind_by_idx / soup_kind_by_idx: {인덱스: 유형} 주입. 없으면 메뉴명 분류.
        staple_category / soup_category: 주식·국 슬롯 카테고리명(Hard 설정과 같은 값).

    Returns:
        AffinitySoftObjective. `.score` 를 다른 Soft 항과 합산해 Maximize.
    """
    w = weights or AffinityWeights()
    rows = table if table is not None else []
    M, D, S = range(len(menus)), range(days), range(n_meals)
    active: dict = {}
    terms = []
    coefficients: dict = {}

    methods = _scd.classify_cooking_methods(menus, override_by_idx=cooking_methods)
    # 조리법 축은 **주찬·부찬만** 본다(Phase 0 집계 범위와 일치).
    side_methods = {m: g for m, g in methods.items()
                    if getattr(menus[m], "category", None) in SIDE_CATEGORIES}

    # ------------------------------------------------------------------ #
    # ②③ 조리법 조합 — 같은 조리법 중복(주력) + 교차 조합(기본 OFF)         #
    # ------------------------------------------------------------------ #
    method_rows = _usable(rows, AXIS_METHOD, w)
    same_coef = {r.value_a: _coef(r.score, w.w_method_same, w.score_divisor)
                 for r in method_rows if r.value_a == r.value_b}
    cross_coef = {(r.value_a, r.value_b): _coef(r.score, w.w_method_cross, w.score_divisor)
                  for r in method_rows if r.value_a != r.value_b}
    by_method: dict = {}
    for m, g in side_methods.items():
        by_method.setdefault(g, []).append(m)

    dup_vars: dict = {}
    cross_vars: dict = {}
    for d in D:
        for s in S:
            used: dict = {}
            for g, members in by_method.items():
                placed = [x[m, d, s] for m in members]
                if not placed:
                    continue
                need_used = any(cross_coef.get(k) for k in cross_coef if g in k)
                if same_coef.get(g):
                    count = sum(placed)
                    dup = model.NewBoolVar(f"affdup_{d}_{s}_{g}")
                    # 완전 반영(양방향) — 리포트가 이 변수를 그대로 읽어도 어긋나지 않게.
                    model.Add(count >= 2).OnlyEnforceIf(dup)
                    model.Add(count <= 1).OnlyEnforceIf(dup.Not())
                    dup_vars[d, s, g] = dup
                    terms.append(same_coef[g] * dup)
                    coefficients[AXIS_METHOD, g, g] = same_coef[g]
                if need_used:
                    used[g] = _scd._new_used_bool(model, f"affused_{d}_{s}_{g}", placed)
            for (a, b), coef in cross_coef.items():
                if not coef or a not in used or b not in used:
                    continue
                z = model.NewBoolVar(f"affcross_{d}_{s}_{a}_{b}")
                model.AddMinEquality(z, [used[a], used[b]])
                cross_vars[d, s, a, b] = z
                terms.append(coef * z)
                coefficients[AXIS_METHOD, a, b] = coef
    active["method_same"] = bool(dup_vars)
    active["method_cross"] = bool(cross_vars)

    # ------------------------------------------------------------------ #
    # ⑤ 주식유형 × 국유형                                                  #
    #   ⚠ H-4c(Hard "찌개·전골·탕은 밥과만")가 켜져 있으면 비밥류+stew 조합이  #
    #   애초에 불가능하고, 남는 rice 의 soup/stew 상대점수는 -1 → 계수 0 이라  #
    #   **기여가 0 이다**. 표를 코드가 아닌 데이터로 두었으므로, H-4c 가 완화되면 #
    #   이 항이 코드 변경 없이 그 자리를 대신한다.                             #
    # ------------------------------------------------------------------ #
    staple_soup = _relative_staple_soup(_usable(rows, AXIS_STAPLE_SOUP, w))
    pair_vars: dict = {}
    if staple_soup and w.w_staple_soup:
        kinds_s = _group_by_kind(menus, M, _mt.classify_staple_kind,
                                 category=staple_category, override=staple_kind_by_idx)
        kinds_u = _group_by_kind(menus, M, _mt.classify_soup_kind,
                                 category=soup_category, override=soup_kind_by_idx)
        for d in D:
            for s in S:
                for (sk, uk), score in staple_soup.items():
                    coef = _coef(score, w.w_staple_soup, w.score_divisor)
                    if not coef or sk not in kinds_s or uk not in kinds_u:
                        continue
                    a = _scd._new_used_bool(
                        model, f"affstaple_{d}_{s}_{sk}", [x[m, d, s] for m in kinds_s[sk]])
                    b = _scd._new_used_bool(
                        model, f"affsoup_{d}_{s}_{uk}", [x[m, d, s] for m in kinds_u[uk]])
                    z = model.NewBoolVar(f"affss_{d}_{s}_{sk}_{uk}")
                    model.AddMinEquality(z, [a, b])
                    pair_vars[d, s, sk, uk] = z
                    terms.append(coef * z)
                    coefficients[AXIS_STAPLE_SOUP, sk, uk] = coef
    active["staple_soup"] = bool(pair_vars)

    # ------------------------------------------------------------------ #
    # ① 주식유형 × 김치 — 기본 OFF                                          #
    #   김치 1개 고정(8/12 영양사 확정, 2026-08-15 사용자가 유지 결정)이 살아  #
    #   있으면 '김치있음'이 상수라, 이 항은 어울림이 아니라 **빵·면 주식 억제**로 #
    #   작동한다(bread+김치있음 -55). 김치를 (0,1)로 완화(H-4d)할 때만 켤 것.   #
    # ------------------------------------------------------------------ #
    active["staple_kimchi"] = False
    if w.w_staple_kimchi:
        kimchi_rows = [r for r in _usable(rows, AXIS_STAPLE_KIMCHI, w)
                       if r.value_b == KIMCHI_PRESENT]
        kinds_s = _group_by_kind(menus, M, _mt.classify_staple_kind,
                                 category=staple_category, override=staple_kind_by_idx)
        kimchi_idx = [m for m in M if getattr(menus[m], "category", None) == _mt.KIMCHI]
        for d in D:
            for s in S:
                for r in kimchi_rows:
                    coef = _coef(r.score, w.w_staple_kimchi, w.score_divisor)
                    if not coef or r.value_a not in kinds_s or not kimchi_idx:
                        continue
                    a = _scd._new_used_bool(
                        model, f"affk_s_{d}_{s}_{r.value_a}",
                        [x[m, d, s] for m in kinds_s[r.value_a]])
                    b = _scd._new_used_bool(
                        model, f"affk_k_{d}_{s}", [x[m, d, s] for m in kimchi_idx])
                    z = model.NewBoolVar(f"affk_{d}_{s}_{r.value_a}")
                    model.AddMinEquality(z, [a, b])
                    pair_vars[d, s, r.value_a, KIMCHI_PRESENT] = z
                    terms.append(coef * z)
                    coefficients[AXIS_STAPLE_KIMCHI, r.value_a, KIMCHI_PRESENT] = coef
                    active["staple_kimchi"] = True

    return AffinitySoftObjective(
        score=sum(terms) if terms else 0,
        active_terms=active,
        method_dup_vars=dup_vars,
        method_cross_vars=cross_vars,
        staple_soup_vars=pair_vars,
        method_by_idx=side_methods,
        coefficients=coefficients,
    )


def _group_by_kind(menus, M, classifier, *, category: str, override: dict | None) -> dict:
    """카테고리가 맞는 메뉴를 유형별로 묶는다 {유형: [인덱스]}.

    유형은 주입값(override) 우선, 없으면 메뉴명 분류 — H-4b·H-4c 와 같은 규약.
    """
    groups: dict = {}
    for m in M:
        if getattr(menus[m], "category", None) != category:
            continue
        if override is not None and m in override:
            kind = override[m]
        else:
            kind = classifier(getattr(menus[m], "name", "") or "")
        if kind:
            groups.setdefault(kind, []).append(m)
    return groups


# ===========================================================================
# (C) 리포팅 — 풀린 해에서 실제로 발생한 어울림 감점
# ===========================================================================
def evaluate_affinity_breakdown(
    solver,
    x: dict,
    menus: list,
    aff: AffinitySoftObjective,
    *,
    days: int,
    n_meals: int,
) -> dict:
    """풀린 해의 어울림 지표를 사람이 읽을 형태로 요약한다.

    ⚠ 라벨·계수는 **제약이 쓴 것**(`aff.method_by_idx`·`aff.coefficients`)만 읽는다.
    여기서 메뉴를 다시 분류하면 주입 경로가 생겼을 때 표만 조용히 어긋난다.
    """
    dup_events, cross_events, pair_events = [], [], []
    penalty = 0
    for (d, s, g), var in aff.method_dup_vars.items():
        if int(solver.Value(var)):
            coef = aff.coefficients.get((AXIS_METHOD, g, g), 0)
            penalty += coef
            dup_events.append({"day": d + 1, "meal_index": s, "method": g,
                               "menus": _picked_names(solver, x, menus, aff, d, s, g),
                               "score": coef})
    for (d, s, a, b), var in aff.method_cross_vars.items():
        if int(solver.Value(var)):
            coef = aff.coefficients.get((AXIS_METHOD, a, b), 0)
            penalty += coef
            cross_events.append({"day": d + 1, "meal_index": s,
                                 "methods": [a, b], "score": coef})
    for (d, s, a, b), var in aff.staple_soup_vars.items():
        if int(solver.Value(var)):
            coef = (aff.coefficients.get((AXIS_STAPLE_SOUP, a, b))
                    or aff.coefficients.get((AXIS_STAPLE_KIMCHI, a, b), 0))
            penalty += coef
            pair_events.append({"day": d + 1, "meal_index": s,
                                "pair": [a, b], "score": coef})
    return {
        "active_terms": aff.active_terms,
        # 감점 대상이 된 조리법 라벨. 근거표에 신뢰할 관측이 없는 축(끓이기·부침)은
        # 여기 없고 **감점도 없다** — 검수 자료에서 "중복 0건"을 이 범위로 읽어야 한다.
        "penalized_methods": sorted({a for (ax, a, b) in aff.coefficients
                                     if ax == AXIS_METHOD and a == b}),
        "method_duplicate_meals": dup_events,
        "method_duplicate_count": len(dup_events),
        "method_cross_pairs": cross_events,
        "staple_pairs": pair_events,
        "affinity_score": penalty,
        "method_coverage": len(aff.method_by_idx),
    }


def _picked_names(solver, x, menus, aff, d, s, method) -> list:
    """그 끼니에서 해당 조리법으로 편성된 메뉴명(중복 사유 확인용)."""
    return sorted(menus[m].name for m, g in aff.method_by_idx.items()
                  if g == method and int(solver.Value(x[m, d, s])))
