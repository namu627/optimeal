"""
routers/menu.py
===============
식단 생성 API (FR-11/FR-12) — 모듈 3 CSP Solver 위임.

모듈 3은 **팀 repo `namu627/optimeal`** 에서 개발 중이므로 이 백엔드는 소스 경로를
런타임에 탐색해 붙인다(`config.module3_src_path()`). 미구성 환경에서도 앱은 기동해야
하므로, 붙이지 못하면 이 엔드포인트만 503 + 구체 사유를 반환한다.

`with_alternatives=true` 면 `alternative_menu.derive_alternative_menus` 로
공통식 + 알레르기 그룹별 대체식 트랙을 함께 산출한다(PRD FR-11 '공통식+대체식 분리').
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import asdict, is_dataclass, replace as dc_replace

from fastapi import APIRouter, HTTPException, Query

from .. import config, schemas

router = APIRouter(prefix="/api/menu", tags=["menu"])

# ── 실서버 풀이 속도 설정 (module_3 MealPlanRequest 의 opt-in 필드) ─────────────
#   module_3 라이브러리 기본값은 그대로(조기 종료 OFF)이고, **API 경로에서만** 켠다.
#   2026-09-26 계측: 7일은 첫 가능해를 수 초 안에 찾고 나머지를 목적값 개선에 써서 한도까지
#   돌았다(최적성 미증명). 아래 기준에서 멈춰도 반환 해는 Hard 제약을 모두 지키는 가능해다.
#   · gap 5%: 증명된 상한 대비 5% 이내면 종료.
#   · 정체: 최근 8초 개선폭이 0.5% 미만이면 종료(3식·31일은 상한이 느슨해 gap 이 안 걸리고,
#     7일은 +0.1%씩 찔끔 오르는 해가 계속 나와 "개선 0" 기준으로는 한도까지 돈다).
#   · presolve 1회: 3식 모델은 presolve 3회(~15초) 뒤에야 첫 해가 나와서 1회로 줄인다.
#   · 시간 상한(요청에 없을 때): 7일 이하 30초 · 그 이상 60초. 7일은 30초 시점 목적값이
#     120초 풀이 최종값의 97~99%였다.
#   종료 사유는 응답 stop_reason 으로 남는다.
SOLVER_GAP_LIMIT = 0.05
SOLVER_STALL_SECONDS = 8.0
SOLVER_STALL_MIN_IMPROVEMENT = 0.005
SOLVER_PARAMS = {"max_presolve_iterations": 1}
# 여러 끼(2식 이상) 전용 가벼운 presolve (2026-10-01).
#   CP-SAT 로그상 7일 3식은 presolve 에만 ~11.5초(Probe 4.9초 + FindBig*LinearOverlap 3.7초)를 쓰고, 그 직후 0.5초 만에
#   첫 해가 나왔다 — 첫 해까지 ~12초라 요청 기준 ~25초에야 해가 생겨, 40초 총 한도 안의 여유가 PC 부하(브라우저·테스트
#   동시 실행)에 다 먹혀 UNKNOWN 이 났다. 두 단계를 끄면 첫 해 3.3~3.5초, 목적값도 2,950~2,989 → 3,953~3,996 으로 오른다
#   (탐색에 쓸 시간이 늘어서). 점심만(1식)은 presolve 가 병목이 아니고, 7일 점심은 끄면 개선이 계속 이어져
#   정체 종료가 늦어지므로(22~24초 → 32초) 1식에는 적용하지 않는다.
SOLVER_PARAMS_MULTI_MEAL = {"cp_model_probing_level": 0, "find_big_linear_overlap": False}
# 긴 기간(7일 초과)·여러 끼 전용 — presolve 를 끈다(2026-10-02).
#   31일 3식은 31일치 완성 힌트가 있어도 presolve 에 ~19초를 쓰고 그 직후 힌트 그대로 첫 해가 나왔다(CP-SAT 로그).
#   모델 구성 ~26초 + 힌트 ~32초 뒤라 80초 총 한도를 넘겼다. presolve 를 끄면 탐색이 ~3.6초에 시작해 첫 해가 ~8초.
#   7일 이하는 presolve 가 탐색 품질에 도움이 되고 시간도 충분해 그대로 둔다.
SOLVER_PARAMS_LONG_MULTI_MEAL = {"cp_model_presolve": False}


def _is_long_multi(days: int, meals) -> bool:
    """7일 초과 + 2식 이상 — 힌트 몫·presolve 를 따로 잡는 요청."""
    return days > 7 and len(meals) >= 2


def _solver_params(meals, days: int = 1) -> dict:
    """끼니 수·기간에 맞춘 CP-SAT 파라미터(요청마다 새 dict)."""
    return {**SOLVER_PARAMS, **(SOLVER_PARAMS_MULTI_MEAL if len(meals) >= 2 else {}),
            **(SOLVER_PARAMS_LONG_MULTI_MEAL if _is_long_multi(days, meals) else {})}


# ── 동시 풀이 직렬화 · 총 소요 한도 (2026-09-30) ─────────────────────────────
#   원인: 7일 3식 기본 조건이 단독으로는 FEASIBLE 인데, 요청 두 개가 겹치면 CP-SAT 둘이 4코어를 나눠 써서
#   둘 다 30초 안에 첫 해를 못 찾고 UNKNOWN 이 됐다(실서버 재현: 동시 2회 → UNKNOWN 2회, 단독 → FEASIBLE).
#   · 풀이는 한 번에 하나만 돈다. SOLVER_BUSY_WAIT_SEC 안에 차례가 안 오면 429(solver_busy)로 바로 알린다
#     — 줄 세워 기다리게 하면 두 번째 요청은 90초 클라이언트 타임아웃을 넘긴다.
#   · 요청에 solver_time_limit 이 없으면 **요청 시작부터의 총 한도**를 module_3 deadline 으로 건다.
#     3식 7일은 모델 구성에만 ~6초를 써서 풀이 30초 + 구성·후처리로 응답이 39~46초였다 → 7일 이하 34초로 시작했으나,
#     브라우저·Vite·도커가 함께 도는 PC(4코어)에서 화면 생성 3회 중 1회 UNKNOWN 이 나와 40초로 늘렸다(2026-09-30).
#     7일 초과는 프론트 axios 타임아웃(90초) 안에 들어오도록 80초(풀이 자체 상한 60초는 그대로).
_SOLVE_LOCK = threading.Lock()
SOLVER_BUSY_WAIT_SEC = 3.0
TOTAL_TIME_BUDGET_SHORT = 40.0      # 7일 이하
TOTAL_TIME_BUDGET_LONG = 80.0       # 7일 초과
GUARD_RELAX_MIN_SECONDS = 10.0      # 울타리 해제 재풀이에 최소로 보장하는 시간
# 웜스타트 힌트: 본 풀이는 **7일치 완성 힌트**가 있어야 시간 안에 첫 해를 찾는다(6일치로 잘리면 UNKNOWN).
#   하루 부분 문제 상한 2초→1초로 힌트 10~12초→약 8초, 힌트 몫 0.35→0.5 로 잘림을 막는다(3/3 완성 확인).
HINT_PER_DAY_TIME = 1.0
HINT_BUDGET_RATIO = 0.5
# 7일 초과·2식 이상: 힌트가 하루 ~1초씩 31일치를 다 만들어야 첫 해가 나온다. 몫 0.5 로는 22초에 18~19일(3식)·
#   23일(2식, 3회 중 1회)에서 잘려 UNKNOWN → 0.85(남은 시간의 85%까지). presolve 를 끄므로(SOLVER_PARAMS_LONG_MULTI_MEAL)
#   완성 힌트면 본 풀이 첫 해까지 수 초면 된다. 하루 상한은 1.0 그대로 — 0.5 이하로 줄이면 하루치를 못 풀어 힌트가 끊긴다.
HINT_BUDGET_RATIO_LONG_MULTI = 0.85


def _hint_budget_ratio(days: int, meals) -> float:
    return HINT_BUDGET_RATIO_LONG_MULTI if _is_long_multi(days, meals) else HINT_BUDGET_RATIO


def _total_deadline(payload, started: float) -> float | None:
    """요청 시작 시각 기준 총 소요 마감(time.monotonic). solver_time_limit 을 명시한 요청은 None(기존 동작)."""
    if payload.solver_time_limit is not None:
        return None
    return started + (TOTAL_TIME_BUDGET_SHORT if payload.days <= 7 else TOTAL_TIME_BUDGET_LONG)


def _solver_time_limit(payload) -> float:
    """요청의 풀이 시간 상한. 명시값이 있으면 그대로, 없으면 일수 기준 기본값."""
    if payload.solver_time_limit is not None:
        return payload.solver_time_limit
    return 30.0 if payload.days <= 7 else 60.0


# 예산을 요청에서 아예 빼면 쓰는 기본값 — "1인 1끼" 기준. 솔버(H-3)는 1인 1일 상한을 받으므로
#   × 끼니 수로 넘긴다. 예전 기본값 3,500원은 1일 값으로 넘어가 3식이면 끼니당 약 1,167원이 되어,
#   실제 시세 원가(2026-09-30 KAMIS 적재 후)로는 3식 7일이 INFEASIBLE 로 증명됐다.
DEFAULT_BUDGET_PER_MEAL_WON = 3500.0

# 이월(carryover) 모드 끼니 원가 울타리(1끼 예산 B 배수, Hard). None 이면 울타리 없음.
#   2026-09-30 폭 스윕(없음 / 0.5~1.5 / 0.6~1.4 / 0.7~1.3 × 7일3식 B3,500·3,000 각 2회, 7일1식, 31일1식):
#   모든 폭에서 INFEASIBLE·UNKNOWN 0, 7일 ≤30.3초·31일 ≤47.9초 → 기준을 만족하는 가장 좁은 0.7~1.3 채택.
#   7일3식 B3,500 끼니 원가 1,337~5,789 → 2,460~4,520, Soft 밴드 밖 10 → 5~6끼, 연속일 최대 원가 차 2,238~3,266 → 1,110~1,746.
#   연속일 균형 Soft 를 끈 비교는 7일3식에서 지표가 나빠져(밴드 밖 7~8, 원가 차 1,650~1,938) 켠 채로 둔다.
CARRYOVER_GUARD: tuple[float, float] | None = (0.7, 1.3)


def _budget_limit_per_day(payload, meals) -> float | None:
    """요청 예산 → 솔버에 넘길 1인 1일 상한.

    필드를 **생략**했을 때만 기본값(1끼 3,500원 × 끼니 수)을 쓴다. 명시한 값은 그대로(프론트는
    이미 한 끼 예산 × 끼니 수를 1일 값으로 보낸다 — toWireRequest), 명시한 null 은 예산 미적용.
    """
    if "budget_limit_per_person" not in payload.model_fields_set:
        return DEFAULT_BUDGET_PER_MEAL_WON * len(meals)
    return payload.budget_limit_per_person


def _budget_plan(payload, meals, cs) -> dict:
    """예산 모드 해석 → {mode, per_day, per_meal, total, carryover_on}.

    · day      : 기존 그대로 — 1일 상한 per_day 를 매일 Hard 로.
    · carryover: 기간 총액 Hard(per_day × 일수 = B×M×D, budget_period="total") + 끼니 밴드·연속일
                 균형 Soft(module_3 budget_carryover). B(1끼 예산) = per_day ÷ 끼니 수.
    예산이 없으면(명시적 null) 어느 모드든 예산 항을 걸지 않는다. module_3 가 이월 모듈이 없는
    구버전이면 day 로 떨어진다(응답 budget_mode 에 실제 적용 모드가 남는다).
    """
    per_day = _budget_limit_per_day(payload, meals)
    mode = payload.budget_mode
    if mode == "carryover" and getattr(cs, "bc", None) is None:
        mode = "day"
    on = mode == "carryover" and per_day is not None
    return {
        "mode": mode,
        "per_day": per_day,
        "per_meal": (per_day / len(meals)) if per_day is not None else None,
        "total": (per_day * payload.days) if per_day is not None else None,
        "carryover_on": on,
    }


def _load_module3():
    """모듈 3 (csp_solver, csp_hard_constraints, alternative_menu) 를 로드한다.

    Returns:
        (csp_solver, csp_hard_constraints, alternative_menu) 모듈 튜플.

    Raises:
        HTTPException(503): 경로 미발견 또는 import 실패(미병합 모듈 등).
    """
    path = config.module3_src_path()
    if path is None:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "module_3_not_found",
                "message": "모듈 3 CSP 소스를 찾지 못했습니다. OPTIMEAL_MODULE3_PATH 를 설정하세요.",
                "hint": "팀 repo namu627/optimeal 의 module_3/src (develop 기준)",
            },
        )
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    try:
        import alternative_menu as am
        import csp_hard_constraints as hc
        import csp_solver as cs
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "module_3_import_failed",
                "message": f"모듈 3 import 실패: {exc}",
                "hint": (
                    "csp_solver.py 는 soft_constraints(ksm)·soft_constraints_diversity(nyc)·"
                    "csp_hard_constraints(pmy) 를 모두 요구합니다. 세 브랜치가 develop 에 "
                    "병합되어야 통합 실행이 가능합니다."
                ),
            },
        ) from exc
    return cs, hc, am


def _to_jsonable(obj):
    """dataclass/set 을 JSON 직렬화 가능한 형태로 변환한다."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _load_menu_candidates(cs, month):
    """메뉴 후보를 조회한다(영양성분 DB 필요).

    Raises:
        HTTPException(503): DB 미기동·미적재.
    """
    try:
        menus = cs.load_menus(month=month)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "menu_source_unavailable",
                "message": f"메뉴 후보 조회 실패: {exc}",
                "hint": "docker-compose 로 PostgreSQL 기동 + nutrition_recipe 적재가 필요합니다.",
            },
        ) from exc
    _reclassify_sides(menus)
    return menus


def _reclassify_sides(menus) -> None:
    """조회 직후 '반찬' 통합 카테고리를 메뉴명 기준으로 주찬/부찬/김치로 나눈다(런타임 한정).

    DB(`nutrition_recipe.menu_category`)는 건드리지 않는다 — 원래는
    `scripts/reclassify_menu_categories.py --apply` 가 할 일이지만, 그 스크립트는
    `original_data.grouping_type` 이 있는 행만 이동 대상으로 삼는데 현재 적재된
    303,369건 전부 이 값이 NULL이라(2026-09-20 확인) 실행해도 0건만 이동되어
    무력화된다. 반상 구성(H-4, 주찬1·부찬2~3·김치1)이 요구하는 카테고리가 DB에
    전혀 없으면 그 즉시 조건과 무관하게 항상 INFEASIBLE 이 되므로, 여기서 이름
    기반 규칙(module_3의 menu_taxonomy.classify_side_kind — grouping_type 없이도
    동작)으로 최소한의 후보를 만든다.
    ⚠ 이걸로도 완전히 해결되진 않는다 — 재분류해도 '김치'로 분류되는 실제 후보가
    DB 전체에 1건뿐이라(원본 데이터 자체의 편중), 메뉴 중복 회피 제약
    (menu_repeat_window_days=3)과 구조적으로 충돌해 2일 이상 지평은 여전히
    INFEASIBLE 이다. 데이터 보강 또는 module_3 팀의 정책 조정이 필요하다.
    """
    try:
        import menu_taxonomy as mt
    except Exception:
        return
    for m in menus:
        if m.category == "반찬":
            m.category = mt.classify_side_kind(m.name)


def _load_main_ingredients(menus) -> dict | None:
    """메뉴별 대표 주재료를 {메뉴인덱스: 재료명}으로 조회한다. 실패하면 None(항 비활성).

    나트륨과 달리 **없어도 안전하다** — 주재료 항은 Soft 라 미상 메뉴가 중립일 뿐이다.
    현재 커버리지는 CSP 후보 960종 중 577종(60.1%). 적용 여부는 응답의
    diversity_breakdown.active_terms.main 으로 드러난다(B6 Phase 1).
    """
    try:
        import csp_solver as cs
        import soft_constraints_diversity as scd

        return scd.load_main_ingredients(cs.get_engine(), menus) or None
    except Exception:
        return None


def _load_profiles() -> dict:
    """급식 대상 프로파일 표를 읽는다(B: 열량·나트륨 기준의 출처).

    실패하면 빈 dict — 프로파일 없이도 payload 의 target_kcal_per_day 로 동작해야 한다.

    ⚠ 모듈 3 경로를 **여기서도** 붙인다. `_load_module3()` 을 거치지 않는 호출부
    (GET /profiles)가 있어, 그것만 믿으면 프로파일이 조용히 비어 503 이 난다.
    """
    try:
        path = config.module3_src_path()
        if path is not None and str(path) not in sys.path:
            sys.path.insert(0, str(path))
        import user_profiles as up

        return up.load_profiles()
    except Exception:
        return {}


def _resolve_targets(payload):
    """프로파일 + 끼니 수 → (끼니 이름들, 목표 kcal, 나트륨 상한, 끼니 비율, 근거, 단백질 목표 g).

    단백질 목표는 제약에 쓰지 않고 응답(applied_targets.protein_g)에만 싣는다 —
    프론트 달성률 게이지가 임의 계산 대신 프로파일 기준값을 쓰게 하기 위함이다.

    프로파일을 주면 payload 의 target_kcal_per_day·sodium_max_mg_per_day 를 **덮는다**.
    끼니를 줄이면 목표도 함께 줄어야 하기 때문이다 — 안 그러면 한 끼에 하루치가 몰린다.
    """
    profiles = _load_profiles()
    profile = profiles.get(payload.profile_key) if payload.profile_key else None
    if payload.profile_key and profile is None:
        raise HTTPException(
            status_code=422,
            detail={"reason": "unknown_profile_key",
                    "message": f"알 수 없는 프로파일: {payload.profile_key}",
                    "available": sorted(profiles)},
        )
    if payload.meals:
        meals = tuple(payload.meals)
    elif profile is not None:
        meals = {1: ("점심",), 2: ("점심", "저녁")}.get(
            profile.default_meals, ("아침", "점심", "저녁"))
    else:
        meals = ("아침", "점심", "저녁")
    if profile is None:
        return meals, payload.target_kcal_per_day, payload.sodium_max_mg_per_day, None, None, None
    import user_profiles as up

    tg = up.targets_for(profile, meals)
    return (meals, tg["target_kcal_per_day"], tg["sodium_max_mg_per_day"],
            tg["meal_energy_ratios"], {"profile": profile.group_name,
                                       "basis": tg["basis"], "source": profile.source,
                                       "note": profile.note},
            tg["protein_min_g"])


def _load_affinity_table() -> list | None:
    """메뉴 어울림 근거표를 읽는다(B6 Phase 2). 실패하면 None(항 비활성).

    주재료와 같은 이유로 **없어도 안전하다** — Soft 라 감점이 없을 뿐이다.
    적용 여부는 응답의 affinity_breakdown.active_terms 로 드러난다.
    """
    try:
        import menu_affinity as ma

        return ma.load_affinity_table() or None
    except Exception:
        return None


def _load_sodium(menus) -> dict | None:
    """메뉴별 나트륨(mg)을 {메뉴인덱스: 값}으로 조회한다. 실패하면 None(제약 미적용).

    조회에 실패했는데 상한만 켜면 H-2e 결측=배제 정책 때문에 전 메뉴가 배제되어
    INFEASIBLE 이 된다. 그래서 "못 읽으면 제약을 켜지 않는다"로 처리하고,
    적용 여부는 응답의 hard_breakdown.active_terms.nutrient_max 로 드러낸다.
    """
    try:
        import csp_solver as cs
        import soft_constraints_diversity as scd

        sodium, _ = scd.load_nutrition_fields(cs.get_engine(), menus)
        return sodium or None
    except Exception:
        return None


def _canonical_by_name(menus) -> dict:
    """메뉴명 -> 대표 후보(MenuItem). 동명 메뉴(같은 recipe_name, 다른 nutrition_id)를 한 행으로 정규화한다.

    응답의 plan 은 메뉴 **이름**만 담으므로, 이름으로 행을 다시 찾는 곳(menu_nutrition·menu_recipes·
    대체식)이 각자 다른 행을 집으면 같은 메뉴의 원가·영양이 서로 어긋난다(예: 가지볶음 303234=0원 /
    303292=132원 → 본식단 259원 vs 대체식 127원). 세 곳 모두 이 대표행을 쓰게 해서 값을 맞춘다.

    선택 규칙(앞에서부터): 원가(cost_won)>0 → 재료(레시피 연결) 보유 → menu_id 오름차순.
    두 번째 기준은 둘 다 원가 0원인 동명(깻잎장아찌롤)에서 레시피 없는 행이 뽑혀 조리 지시서가
    '레시피 없음'이 되는 것을 막는다. 현재 동명 6쌍은 모두 한쪽만 레시피·가격이 연결돼 있다.

    ⚠ Level 1(표시 정규화)이다. 솔버는 여전히 두 행을 별개 후보로 풀기 때문에, 솔버가 대표행이 아닌
    쪽(0원 행)을 고르면 total_cost_won(솔버가 실제 고른 행 기준)과 여기 값이 다를 수 있다.
    Level 2: module_3 가 res.plan_ids(솔버가 고른 menu_id)를 주면 본식단의 영양·레시피는 id 로
    조회하고(generate 참고), 이 대표행은 ① plan_ids 가 없는 구버전 module_3 대비책 ② 이름 키
    필드의 plan 밖 메뉴 ③ 대체식(alternative_menu 는 아직 이름 기반 — module_3 id화 전까지)에만 쓴다.
    """
    best: dict = {}
    for m in menus:
        cur = best.get(m.name)
        if cur is None or _canonical_rank(m) < _canonical_rank(cur):
            best[m.name] = m
    return best


def _canonical_rank(m) -> tuple:
    """_canonical_by_name 정렬 키 — 작을수록 대표행으로 우선."""
    return (not (getattr(m, "cost_won", 0) or 0) > 0,
            not getattr(m, "ingredients", None),
            getattr(m, "menu_id", 0) or 0)


def _nutrition_by_id(menus) -> dict:
    """menu_id -> {name, kcal, protein, sodium, cost, cost_exact}. 전 후보(동명 행 각각)를 id 로 구분해 싣는다.
    calories·cost 는 후보(MenuItem)에 이미 있고, protein·sodium 은 nutrition_recipe 에서 보강한다.
    조회에 실패해도 kcal·cost 는 채우고 protein·sodium 만 None 으로 둔다 — 응답은 항상 나간다.
    """
    prot: dict = {}
    sod: dict = {}
    try:
        import csp_solver as cs
        from sqlalchemy import text

        ids = [m.menu_id for m in menus if getattr(m, "menu_id", None) is not None]
        if ids:
            q = text("SELECT nutrition_id, protein, sodium FROM nutrition_recipe "
                     "WHERE nutrition_id = ANY(:ids)")
            with cs.get_engine().connect() as conn:
                for r in conn.execute(q, {"ids": ids}).mappings():
                    if r["protein"] is not None:
                        prot[r["nutrition_id"]] = float(r["protein"])
                    if r["sodium"] is not None:
                        sod[r["nutrition_id"]] = float(r["sodium"])
    except Exception:
        pass
    out: dict = {}
    for m in menus:
        out[m.menu_id] = {
            "name": m.name,
            "kcal": round(float(getattr(m, "calories", 0) or 0), 1),
            "protein": prot.get(m.menu_id),
            "sodium": sod.get(m.menu_id),
            "cost": round(float(getattr(m, "cost_won", 0) or 0)),
            # 반올림 전 원가 — 프론트가 합계를 반올림 전 값으로 더해 서버 총액(total_cost_won)과 맞추게 한다.
            "cost_exact": round(float(getattr(m, "cost_won", 0) or 0), 4),
        }
    return out


def _menu_nutrition_by_name(nut_by_id: dict, canon: dict, plan_ids: dict | None) -> dict:
    """메뉴명 -> {kcal, protein, sodium, cost} (기존 응답 필드 menu_nutrition, 호환용).

    기본은 동명 대표행(canon) 값이고, plan_ids 가 있으면 본식단에 실제로 오른 메뉴는 솔버가 고른
    행 값으로 덮는다 — 이름 키만 보는 구버전 프론트도 본식단 칸에서는 솔버와 같은 값을 보게 된다.
    (같은 이름의 두 행이 한 식단에 함께 오르면 이름 키로는 하나만 담긴다 → id 키 필드를 쓸 것.)
    """
    def strip(v: dict) -> dict:
        return {k: v[k] for k in ("kcal", "protein", "sodium", "cost")}

    out = {name: strip(nut_by_id[m.menu_id]) for name, m in canon.items() if m.menu_id in nut_by_id}
    for mid in _iter_plan_ids(plan_ids):
        if mid in nut_by_id:
            out[nut_by_id[mid]["name"]] = strip(nut_by_id[mid])
    return out


def _iter_plan_ids(plan_ids: dict | None):
    """plan_ids({day:{meal:[id,...]}})의 id 를 등장 순서대로 낸다."""
    for meals in (plan_ids or {}).values():
        for ids in meals.values():
            yield from ids


def _make_recipe_of_by_id(engine):
    """menu_id(nutrition_id) -> 레시피를 조회하는 recipe_of (cooking_sheet 주입용).

    module_3 의 cooking_sheet.make_db_recipe_of 는 `WHERE nr.recipe_name = :menu` 로 조회해
    동명 행이 여럿이면 모든 행의 재료를 섞을 수 있다. 여기서는 nutrition_id 하나로만 조회한다.
    cooking_sheet.build_cooking_sheet 는 plan 값을 해석하지 않고 recipe_of 에 그대로 넘기므로
    plan_ids 를 plan 자리에 넣으면 솔버가 고른 행 기준 조리 지시서가 된다.
    반환 형식은 make_db_recipe_of 와 동일하다. 레시피가 없으면 None → '레시피 없음'.
    """
    from sqlalchemy import text

    query = text("""
        SELECT cm.method_name AS cooking_method,
               ing.ingredient_name AS name,
               rim.per_serving_grams AS base_amount_g,
               rim.unit AS unit,
               rim.ingredient_role AS role,
               rim.cooking_step_order AS step
        FROM recipe r
        JOIN recipe_ingredient_map rim ON rim.recipe_id = r.recipe_id
        JOIN ingredient ing          ON ing.ingredient_id = rim.ingredient_id
        LEFT JOIN cooking_method cm   ON cm.method_id = r.primary_method_id
        WHERE r.nutrition_recipe_id = :nid
        ORDER BY rim.cooking_step_order NULLS LAST
    """)

    def recipe_of(menu_id):
        if menu_id is None:
            return None
        with engine.connect() as conn:
            rows = conn.execute(query, {"nid": menu_id}).mappings().all()
        if not rows:
            return None
        return {
            "cooking_method": rows[0]["cooking_method"],
            "ingredients": [{
                "name": r["name"],
                "base_amount_g": float(r["base_amount_g"]) if r["base_amount_g"] is not None else None,
                "unit": r["unit"] or "g",
                "role": r["role"],
                "step": r["step"],
            } for r in rows],
        }
    return recipe_of


def _steps_by_id(engine, ids) -> dict[int, list[str]]:
    """menu_id(nutrition_id) -> 조리 단계 문장 목록(원본 순서).

    출처는 nutrition_recipe.original_data 의 MANUAL01~20 뿐이다 — API 적재분은 식품안전나라 원본,
    소규모 레시피 xlsx 적재분은 scripts/load_cooking_steps_from_recipe_db.py 가 옮긴 cooking_step 원문.
    원문을 다듬거나 만들어 내지 않고, 빈 칸만 건너뛴다. 단계가 없는 메뉴(식약처 영양DB 등)는
    키가 없다 → 호출부에서 빈 목록.
    """
    from sqlalchemy import bindparam, text

    ids = [i for i in ids if i is not None]
    if not ids:
        return {}
    query = text("""
        SELECT nr.nutrition_id AS nid, kv.key AS k, kv.value AS v
        FROM nutrition_recipe nr
        CROSS JOIN LATERAL jsonb_each_text(nr.original_data) kv
        WHERE nr.nutrition_id IN :ids
          AND kv.key ~ '^MANUAL[0-9]+$'
          AND btrim(kv.value) <> ''
    """).bindparams(bindparam("ids", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(query, {"ids": ids}).mappings().all()
    ordered: dict[int, list[tuple[int, str]]] = {}
    for r in rows:
        ordered.setdefault(r["nid"], []).append((int(r["k"][len("MANUAL"):]), r["v"].strip()))
    return {nid: [v for _, v in sorted(steps)] for nid, steps in ordered.items()}


BASIS_SCALED, BASIS_LINEAR = "스케일링", "단순 비례"


def _apply_scaling(engine, recipes: dict, ids: dict, servings: int, site_id: int | None) -> None:
    """메뉴별 재료 총량에 스케일링 API(/api/scaling/predict)와 같은 규칙을 입히고 값마다 기준을 단다.

    레시피 화면·CSV·PDF 조리 지시서가 모두 이 결과(menu_recipes)를 쓰므로 기준은 여기 한 곳에서 정한다.
      - 스케일링: site_id 업장에 그 (레시피, 재료) 캘리브레이션 추정이 있으면 est_ratio × 1인분 × 인원수.
      - 단순 비례: 그 밖 전부(업장 미지정·추정 없음·스케일링 레지스트리에 없는 레시피/재료) — 1인분 × 인원수.
    스케일링 레지스트리 키는 df_B.csv 의 small_recipe_id(= recipe.notes 의 '[orig:A0330]')다. 식약처 API 로만
    적재된 레시피는 키가 없어 늘 단순 비례다. 재료마다 base_g(1인분)·basis 를 붙인다(1인분 = 총량÷인원 이 아님).
    module_2 CalibrationStore 를 읽기만 한다(수정 없음).
    """
    for rec in recipes.values():
        for ing in rec.get("ingredients") or []:
            amt = ing.get("amount")
            ing["base_g"] = None if amt is None else round(float(amt) / servings, 2)
            ing["basis"] = None if amt is None else BASIS_LINEAR
    if site_id is None or not recipes:
        return
    from sqlalchemy import bindparam, text

    from .. import repositories
    from ..deps import _db_path

    nids = [v for v in ids.values() if v is not None]
    if not nids:
        return
    q = text("SELECT nutrition_recipe_id AS nid, substring(notes from '\\[orig:([^\\]]+)\\]') AS k FROM recipe "
             "WHERE nutrition_recipe_id IN :ids AND notes LIKE '[orig:%'").bindparams(bindparam("ids", expanding=True))
    with engine.connect() as conn:
        key_of = {r["nid"]: r["k"] for r in conn.execute(q, {"ids": nids}).mappings()}
    from calibration_store import CalibrationStore  # module_2 (deps.py 가 경로를 잡는다)

    store = CalibrationStore(_db_path())
    try:
        for key, rec in recipes.items():
            rid = repositories.recipe_id_of(key_of.get(ids.get(key)) or "")
            if rid is None:
                continue
            for ing in rec.get("ingredients") or []:
                iid = repositories.ingredient_id_of(ing.get("name") or "")
                if iid is None or ing.get("base_g") is None:
                    continue
                pred = store.predict(site_id, rid, iid, ing["base_g"], servings)
                if pred.method == "calibrated":
                    ing["amount"] = round(pred.scaled_g, 1)
                    ing["basis"] = BASIS_SCALED
    finally:
        store.close()


def _build_menu_recipes(cs, plan: dict, servings: int, key_to_id, site_id: int | None = None) -> dict:
    """확정 plan에 등장하는 메뉴별 재료 투입량(총량)·조리순서(Step3 조리 지시서·레시피 화면용).

    module_3.cooking_sheet.build_cooking_sheet 는 day/meal/menu 단위로 행을 내지만,
    재료 구성은 메뉴(행)에만 의존하므로 여기서 plan 값(키) 기준으로 한 번만 접어 돌려준다
    (프론트가 날짜와 무관하게 조회할 수 있게).

    Args:
        plan: plan_ids({day:{meal:[menu_id]}}) 또는 이름 plan. 반환 dict 의 키가 이 값이 된다.
        key_to_id: plan 값 -> menu_id (plan_ids 면 항등, 이름 plan 이면 대표행 id 조회).

    레시피(recipe_ingredient_map)가 없는 메뉴는 note만 채운 항목으로 남긴다.
    steps 는 원본 조리 단계(_steps_by_id)이며 원본에 없으면 빈 목록이다(임의 생성하지 않음).
    실패해도(DB 미구성 등) 조리 지시서 없이 응답은 나가야 하므로 빈 dict로 저하한다.
    """
    if not plan or not servings:
        return {}
    try:
        import cooking_sheet as csheet

        engine = cs.get_engine()
        recipe_of_id = _make_recipe_of_by_id(engine)
        rows = csheet.build_cooking_sheet(plan, lambda key: recipe_of_id(key_to_id(key)), servings)
    except Exception:
        return {}
    out: dict = {}
    for r in rows:
        out.setdefault(r["menu"], {
            "cooking_method": r["cooking_method"],
            "ingredients": r["ingredients"],
            "note": r["note"],
        })
    ids = {key: key_to_id(key) for key in out}
    try:
        steps = _steps_by_id(engine, set(ids.values()))
    except Exception:
        steps = {}  # 단계 조회 실패는 재료 표시를 막지 않는다
    for key, rec in out.items():
        rec["steps"] = steps.get(ids[key], [])
    try:
        _apply_scaling(engine, out, ids, servings, site_id)
    except Exception:
        pass  # 캘리브레이션 조회 실패 → 단순 비례 그대로(기준 표기는 _apply_scaling 첫 단계에서 이미 붙었다)
    return out


def _derive_alternatives(am, plan, menus, cfg, allergy_groups: list[dict],
                         sodium_by_idx: dict | None = None, canon: dict | None = None) -> list[dict]:
    """공통식 plan 에서 알레르기 그룹별 대체식 트랙을 파생한다(PRD FR-11).

    alternative_menu 는 메뉴명으로 행을 찾는데(by_name, 첫 행 우선) 동명이면 0원 행을 집어
    대체식 총원가가 본식단과 어긋났다. 그래서 후보를 이름당 대표행 하나(canon)로 줄여 넘긴다.
    sodium_by_idx 는 원래 후보(menus) 인덱스 기준이라 menu_id 키로 바꾼 뒤 그대로 쓴다
    (대표행의 menu_id 도 그 안에 있다).
    """
    groups = [
        am.AllergyGroup(
            label=g.get("label", ""),
            allergens=set(g.get("allergens", [])),
            count=int(g.get("count", 0)),
        )
        for g in allergy_groups
    ]
    # 대체식도 공통식과 같은 나트륨 상한을 지켜야 한다 → menu_id 키로 변환해 주입.
    sodium_by_id = ({menus[i].menu_id: v for i, v in sodium_by_idx.items()
                     if i < len(menus)} if sodium_by_idx else None)
    alt_menus = list(canon.values()) if canon else menus
    # 재료 치환(방식2)은 쓰지 않는다 — 이름만 '(대체: 달걀→두부)'로 바뀌고 레시피·조리 순서·분량은 원래 메뉴라
    # 계란찜의 달걀을 두부로 바꾸는 식의 조리 불가 지시서가 나왔다(2026-10-06 결정). 알레르기 자리는 늘 다른 메뉴로 바꾼다.
    alts = am.derive_alternative_menus(plan, alt_menus, groups, hard_config=cfg,
                                       sodium_by_id=sodium_by_id, ingredient_substitution=False)
    return [_to_jsonable(a) for a in alts]


def _alt_plan_ids(alt_plan: dict | None, common_plan: dict | None, common_ids: dict | None,
                  canon: dict) -> dict:
    """대체식 plan(메뉴명만)과 같은 모양의 plan_ids 를 만든다 — 검토 화면 교체 팝오버·영양 조회용.

    alternative_menu 는 메뉴명만 돌려준다. 이름만으로는 교체 후보(/candidates, id 기반)를 부를 수 없어
    대체식 칸은 '교체 후보를 불러올 수 없어요'가 떴다. 칸마다 id 를 이렇게 붙인다.
      ① 본식단 같은 자리와 이름이 같으면(바뀌지 않은 메뉴) 솔버가 고른 행 id 그대로
      ② 재료 치환 접시 '메뉴(대체: …)' 는 원래 메뉴의 id(같은 메뉴·같은 자리)
      ③ 다른 메뉴로 대체된 칸은 이름당 대표행(canon) id
    못 찾으면 None(프론트는 그 칸만 교체 불가로 표시).
    """
    out: dict = {}
    for day, meals in (alt_plan or {}).items():
        out[day] = {}
        for meal, names in (meals or {}).items():
            base = ((common_plan or {}).get(day) or {}).get(meal) or []
            bids = ((common_ids or {}).get(day) or {}).get(meal) or []
            ids = []
            for i, name in enumerate(names):
                key = name.split("(대체:")[0].strip()
                if i < len(base) and i < len(bids) and base[i] in (name, key):
                    ids.append(bids[i])
                    continue
                m = canon.get(name) or canon.get(key)
                ids.append(m.menu_id if m is not None else None)
            out[day][meal] = ids
    return out


# 교체 후보 풀 캐시 — 후보 조회(가격·재료 조인)가 무거워 팝오버를 열 때마다 다시 읽지 않는다.
# 데이터 적재가 바뀌어도 최대 TTL 뒤에는 반영된다.
_POOL_TTL_SEC = 300
_pool_cache: dict = {"at": 0.0, "menus": None}
_pool_lock = threading.Lock()


def _candidate_pool(cs) -> list:
    """generate 와 같은 후보(같은 카테고리 재분류 포함)를 캐시해 돌려준다."""
    with _pool_lock:
        if _pool_cache["menus"] is None or time.monotonic() - _pool_cache["at"] > _POOL_TTL_SEC:
            _pool_cache["menus"] = _load_menu_candidates(cs, None)
            _pool_cache["at"] = time.monotonic()
        return _pool_cache["menus"]


def _parse_ids(raw: str) -> set[int]:
    """'1,2,3' → {1,2,3}. 숫자가 아니면 422."""
    try:
        return {int(x) for x in raw.split(",") if x.strip()}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={
            "reason": "invalid_exclude_ids", "message": f"exclude_ids 는 쉼표로 구분한 정수여야 합니다: {raw}"}) from exc


@router.get("/candidates", summary="메뉴 교체 후보 (같은 자리의 실메뉴)")
def swap_candidates(
    nutrition_id: int = Query(..., description="교체하려는 칸의 메뉴 id(응답 plan_ids 의 값)"),
    exclude_ids: str = Query("", description="제외할 메뉴 id 들(쉼표 구분) — 보통 현재 식단에 이미 있는 메뉴"),
    limit: int = Query(8, ge=1, le=30),
    exclude_allergens: str = Query(
        "", description="제외할 알레르겐(쉼표 구분) — 대체식 칸 교체 시 그 그룹의 알레르겐. 교차반응까지 확장해 거른다"),
    enforce_menu_structure: bool = Query(
        True, description="generate 와 같은 H-4b: 주식 자리는 밥·면·죽·빵만 후보로"),
    plan_total_cost: float | None = Query(
        None, ge=0, description="carryover 예산 판정용: 현재 식단의 기간 총원가(원, 응답 total_cost_won)"),
    total_budget: float | None = Query(
        None, gt=0, description="carryover 예산 판정용: 기간 총예산(원, 응답 applied_targets.budget_total)"),
    meal_cost: float | None = Query(
        None, ge=0, description="carryover 울타리 판정용: 교체하려는 칸이 속한 끼니의 현재 1인 원가(원)"),
    guard_min: float | None = Query(
        None, ge=0, description="carryover 울타리 판정용: 끼니 원가 하한(원, applied_targets.guard_min_won)"),
    guard_max: float | None = Query(
        None, gt=0, description="carryover 울타리 판정용: 끼니 원가 상한(원, applied_targets.guard_max_won)"),
) -> dict:
    """현재 메뉴와 같은 자리(카테고리)의 실제 후보를 돌려준다(검토 화면 교체 팝오버용).

    카테고리는 generate 와 같은 기준이다 — DB 의 '반찬'은 메뉴명으로 주찬/부찬/김치로 나눈 뒤 비교해
    김치 자리에 주찬이 오지 않게 한다. 주식 자리는 솔버의 H-4b(주식 자격)와 같은 판정
    (menu_taxonomy.is_staple)으로 거른다 — DB '주식'에는 스테이크 같은 일품요리도 섞여 있다.
    자기 자신·제외 목록의 메뉴(같은 이름의 다른 행 포함)는 빼고, 동명 메뉴는 대표행 하나로 줄인다.
    정렬: 원가 있는 행 → 현재 메뉴와 열량이 가까운 순 → id.

    예산(carryover): plan_total_cost·total_budget 를 함께 주면 후보마다 교체 후 기간 총액
    (`period_total_after` = 현재 총액 − 현재 메뉴 원가 + 후보 원가)과 `within_total_budget`(≤ 총예산)을 싣는다.
    carryover 모드에서는 한 끼가 B 를 넘어도 기간 총액 안이면 교체 가능하다. 둘 중 하나라도 없으면
    예산 필드를 싣지 않는다(day 모드 한 끼 예산 판정은 프론트 checkSwap 이 한다).
    울타리: meal_cost 와 guard_min/guard_max(하나 이상)를 주면 후보마다 교체 후 끼니 원가(`meal_cost_after`)와
    `within_meal_guard`(울타리 안)를 싣는다.

    ⚠ 그 밖의 제약 재검증은 하지 않는다(열량 밴드·나트륨 상한·3일 중복·H-4c 국 궁합 등). 교체 후 전체
    재검증은 include/exclude_menu_ids 로 다시 푸는 '재생성'이 후속 과제다.

    Raises:
        HTTPException(404): 후보 풀에 없는 nutrition_id. 503: 모듈 3·DB 미구성.
    """
    cs, _, _ = _load_module3()
    menus = _candidate_pool(cs)
    by_id = {m.menu_id: m for m in menus}
    cur = by_id.get(nutrition_id)
    if cur is None:
        raise HTTPException(status_code=404, detail={
            "reason": "menu_not_in_pool", "message": f"식단 후보에 없는 메뉴입니다: nutrition_id={nutrition_id}"})
    excluded = _parse_ids(exclude_ids) | {cur.menu_id}
    excluded_names = {by_id[i].name for i in excluded if i in by_id}
    pool = [m for m in menus
            if m.category == cur.category and m.menu_id not in excluded and m.name not in excluded_names]
    # 대체식 칸: 그 그룹이 피해야 할 알레르겐(교차반응 포함)이 든 메뉴는 후보에서 뺀다
    # (alternative_menu 가 대체 메뉴를 고를 때와 같은 기준).
    # 화면 선택값('갑각류'·'계란' 등)은 19종 표준 이름으로 바꾼 뒤 확장한다(메뉴 allergens 가 표준 이름).
    _, _, am = _load_module3()
    allergens = am.normalize_allergens(exclude_allergens.split(","))
    if allergens:
        unsafe = am._expand_cross_reactive(allergens)
        pool = [m for m in pool if not am.menu_has_unsafe(m, unsafe)]
    if enforce_menu_structure and cur.category == "주식":
        import menu_taxonomy as mt

        pool = [m for m in pool if mt.is_staple(m.name)]
    ranked = sorted(_canonical_by_name(pool).values(),
                    key=lambda m: (not (m.cost_won or 0) > 0, abs((m.calories or 0) - (cur.calories or 0)), m.menu_id))
    picked = ranked[:limit]
    nut = _nutrition_by_id(picked + [cur])

    judge_total = plan_total_cost is not None and total_budget is not None
    judge_guard = meal_cost is not None and (guard_min is not None or guard_max is not None)

    def out(m) -> dict:
        n = nut[m.menu_id]
        row = {"menu_id": m.menu_id, "name": n["name"], "kcal": n["kcal"],
               "protein": n["protein"], "sodium": n["sodium"], "cost": n["cost"], "cost_exact": n["cost_exact"]}
        if judge_total:
            after = plan_total_cost - float(cur.cost_won or 0) + float(m.cost_won or 0)
            row["period_total_after"] = round(after)
            row["within_total_budget"] = after <= total_budget
        if judge_guard:
            meal_after = meal_cost - float(cur.cost_won or 0) + float(m.cost_won or 0)
            row["meal_cost_after"] = round(meal_after)
            row["within_meal_guard"] = ((guard_min is None or meal_after >= guard_min)
                                        and (guard_max is None or meal_after <= guard_max))
        return row

    return {"category": cur.category, "current": out(cur), "candidates": [out(m) for m in picked],
            "total_in_category": len(ranked)}


@router.get("/recipes", summary="메뉴 레시피 조회 (재료 투입량·원본 조리 단계)")
def menu_recipes(
    ids: list[int] = Query([], description="메뉴 id(nutrition_id) — 본식단·교체한 메뉴"),
    names: list[str] = Query([], description="메뉴명 — id 가 없는 대체식 칸. generate 와 같은 대표행으로 해석"),
    servings: int = Query(..., ge=1, description="인원수 — 투입량은 1인분 × 인원수 총량"),
    site_id: int | None = Query(None, ge=1, description="캘리브레이션 업장 id — 보정 있는 재료만 스케일링 총량"),
) -> dict:
    """레시피 화면에서 생성 응답에 레시피가 없는 메뉴(검토에서 교체한 메뉴·대체식)를 채운다.

    generate 의 menu_recipes 와 같은 빌더(_build_menu_recipes)를 쓰므로 모양·값이 같다.
    재료는 recipe_ingredient_map, 조리 단계는 식품안전나라 원본(MANUAL01~20)에 있는 것만 온다.

    Raises:
        HTTPException(422): 요청 메뉴가 100개 초과. 503: 모듈 3·DB 미구성.
    """
    if len(ids) + len(names) > 100:
        raise HTTPException(status_code=422, detail={
            "reason": "too_many_menus", "message": "한 번에 100개 메뉴까지 조회할 수 있습니다."})
    cs, _, _ = _load_module3()
    by_id = _build_menu_recipes(cs, {"1": {"점심": list(dict.fromkeys(ids))}}, servings, lambda mid: mid, site_id) if ids else {}
    by_name: dict = {}
    if names:
        canon = _canonical_by_name(_candidate_pool(cs))

        def id_of(name: str):
            # 재료 치환 접시 '메뉴(대체: …)' 는 원래 메뉴 레시피를 돌려준다 — 치환은 프론트 recipeView.substitutedRecipe 가 입힌다.
            m = canon.get(name) or canon.get(name.split("(대체:")[0].strip())
            return m.menu_id if m is not None else None

        by_name = _build_menu_recipes(
            cs, {"1": {"점심": list(dict.fromkeys(names))}}, servings, id_of, site_id)
    return {"by_id": {str(k): v for k, v in by_id.items()}, "by_name": by_name}


@router.get("/profiles", response_model=list[schemas.UserProfileOut],
            summary="급식 대상 프로파일 목록 (열량·나트륨 기준)")
def list_profiles():
    """영양사가 자기 업장의 급식 대상을 고르는 목록.

    수치 출처는 **2025 한국인 영양소 섭취기준**(보건복지부·한국영양학회, 2025.12)과
    **학교급식법 시행규칙 [별표3]**이다. `source`가 '파생'인 행(혼성)은 남녀 1:1 평균이므로
    실제 성비로 조정해야 하며, 프론트는 `source`·`note`를 값과 함께 표시할 것.

    ⚠ '환자' 프로파일은 **일반식**이다. 치료식(당뇨·신장 등)은 의료진·병원 영양팀이
    정할 사항이라 본 시스템은 기준값을 제공하지 않는다.
    """
    profiles = _load_profiles()
    if not profiles:
        raise HTTPException(
            status_code=503,
            detail={"reason": "profile_table_unavailable",
                    "message": "급식 대상 프로파일 표를 읽지 못했습니다.",
                    "hint": "data/processed/user_group_profiles.csv 존재 여부 확인"},
        )
    return [schemas.UserProfileOut(**vars(p)) for p in profiles.values()]


@router.post("/generate", summary="식단 자동 생성 (모듈 3 CSP 위임)")
def generate(payload: schemas.MenuGenerateRequest) -> dict:
    """CSP Solver로 식단을 생성한다(옵션: 알레르기 그룹별 대체식 동반).

    Args:
        payload: 일수·칼로리 기준·예산·알레르겐·대체식 옵션.

    Returns:
        {status, plan, daily_kcal, total_cost, hard/soft breakdown, alternatives}.

    Raises:
        HTTPException(503): 모듈 3 또는 영양성분 DB 미구성.
    """
    started = time.monotonic()
    cs, hc, am = _load_module3()
    menus = _load_menu_candidates(cs, payload.month)
    meals, kcal, sodium_max, ratios, basis, protein_g = _resolve_targets(payload)
    # H-2e 나트륨 상한: 값을 주입할 수 있을 때만 켠다(결측=배제 정책 → 미주입 시 전 메뉴 배제).
    sodium_by_idx = _load_sodium(menus) if sodium_max else None
    budget = _budget_plan(payload, meals, cs)
    budget_per_day = budget["per_day"]
    cfg = hc.HardConstraintConfig(
        target_kcal_per_day=kcal,
        kcal_tolerance=payload.kcal_tolerance,
        budget_limit_per_person=budget_per_day,
        budget_period="total" if budget["carryover_on"] else "day",
        excluded_allergens=am.normalize_allergens(payload.excluded_allergens),
        nutrient_max_per_day=({"sodium": sodium_max} if sodium_by_idx else {}),
        enable_staple_main=payload.enforce_menu_structure,
        enable_menu_pairing=payload.enforce_menu_structure,
        exclude_menu_ids=set(payload.exclude_menu_ids),
        include_menu_ids=set(payload.include_menu_ids),
    )
    if ratios:
        cfg.meal_energy_ratios = ratios
    req = cs.MealPlanRequest(
        days=payload.days, meals=meals, hard=cfg,
        solver_time_limit=_solver_time_limit(payload),
        relative_gap_limit=SOLVER_GAP_LIMIT,
        stall_seconds=SOLVER_STALL_SECONDS,
        stall_min_improvement=SOLVER_STALL_MIN_IMPROVEMENT,
        solver_params=_solver_params(meals, payload.days),
        hard_nutrient_by_idx=({"sodium": sodium_by_idx} if sodium_by_idx else None),
        main_by_idx=_load_main_ingredients(menus),
        affinity_table=_load_affinity_table(),
    )
    guard = None
    guard_relaxed = False
    if budget["carryover_on"]:
        guard = CARRYOVER_GUARD
        req.carryover = cs.bc.CarryoverConfig(
            per_meal_budget_won=budget["per_meal"],
            sodium_max_per_day=sodium_max if sodium_by_idx else None,
            guard_low=guard[0] if guard else None, guard_high=guard[1] if guard else None)
    if hasattr(req, "deadline"):   # 구버전 module_3 에는 필드가 없다
        req.deadline = _total_deadline(payload, started)
    if hasattr(req, "hint_per_day_time"):
        req.hint_per_day_time = HINT_PER_DAY_TIME
        req.hint_budget_ratio = _hint_budget_ratio(payload.days, meals)
    if not _SOLVE_LOCK.acquire(timeout=SOLVER_BUSY_WAIT_SEC):
        raise HTTPException(status_code=429, detail={
            "reason": "solver_busy",
            "message": "다른 식단을 생성하는 중이에요. 끝난 뒤 다시 시도해 주세요.",
            "hint": "식단 생성은 한 번에 하나씩 풀어요(동시에 풀면 둘 다 시간 안에 해를 못 찾음)."})
    try:
        lock_at = time.monotonic() - started    # 후보 조회·대상 산출에 쓴 시간(진단용)
        res = cs.build_and_solve(menus, req)
        # 안전장치: 울타리 때문에 해가 없다고 **증명**되면(INFEASIBLE) 울타리만 빼고 한 번 더 푼다.
        #   UNKNOWN(시간 안에 못 찾음)은 울타리 탓인지 알 수 없고, 총 한도 안에 다시 풀 시간도 없어 그대로 돌려준다.
        if guard is not None and res.status == "INFEASIBLE":
            req.carryover = dc_replace(req.carryover, guard_low=None, guard_high=None)
            if getattr(req, "deadline", None) is not None:
                req.deadline = max(req.deadline, time.monotonic() + GUARD_RELAX_MIN_SECONDS)
            res = cs.build_and_solve(menus, req)
            guard_relaxed = True
    finally:
        _SOLVE_LOCK.release()
    body = {
        "status": res.status,
        "wall_time_sec": round(res.wall_time, 3),
        # 풀이 종료 사유(optimal·gap·stall·time_limit…). 구버전 module_3 는 필드가 없어 None.
        "stop_reason": "guard_relaxed" if guard_relaxed else getattr(res, "stop_reason", None),
        # 진단용 풀이 시간 내역: 웜스타트(초기해) 구성 초·날 수(days 보다 적으면 시간 몫에 잘린 부분 힌트),
        #   본 풀이 시작→첫 가능해(초), 요청 시작→응답 직전(초). 첫 해가 늦을수록 UNKNOWN 에 가깝다.
        "timing": {"warm_start_sec": getattr(res, "warm_start_seconds", None),
                   "warm_start_days": getattr(res, "warm_start_days", None),
                   "first_solution_sec": getattr(res, "first_solution_seconds", None),
                   "before_solve_sec": round(lock_at, 2),
                   "total_sec": None},
        # 어떤 기준으로 풀었는지 응답에 남긴다 — 영양사가 화면에서 근거를 볼 수 있어야 한다.
        "applied_targets": {
            "meals": list(meals),
            "target_kcal_per_day": kcal,
            "sodium_max_mg_per_day": sodium_max,
            "protein_g": protein_g,  # 프로파일 기준 단백질 목표(제약 아님, 표시용). 프로파일 없으면 None
            # 실제로 적용한 1인 1일 예산 상한(원). 요청에서 생략했으면 1끼 기본값 × 끼니 수. None=미적용
            "budget_limit_per_day": budget_per_day,
            # 예산 모드(실제 적용값)·기간 총예산·끼니당 기준 B. carryover 면 총예산이 Hard 상한이다.
            "budget_mode": budget["mode"],
            "budget_total": budget["total"],
            "budget_per_meal": budget["per_meal"],
            # 끼니 원가 울타리(원, carryover 만). 울타리를 풀고 다시 풀었으면(guard_relaxed) None.
            "guard_min_won": (budget["per_meal"] * guard[0] if guard and not guard_relaxed else None),
            "guard_max_won": (budget["per_meal"] * guard[1] if guard and not guard_relaxed else None),
            "profile": basis,
        },
        "plan": _to_jsonable(res.plan),
        "daily_kcal": _to_jsonable(res.daily_kcal),
        "total_cost_won": res.total_cost,
        "hard_breakdown": _to_jsonable(res.hard_breakdown),
        "soft_breakdown": _to_jsonable(res.soft_breakdown),
        "diversity_breakdown": _to_jsonable(res.diversity_breakdown),
        "affinity_breakdown": _to_jsonable(res.affinity_breakdown),
        # 식단가 이월 리포트(carryover 모드만): 끼니별 원가 배열·최대/최소·B 초과 끼니 수·연속일 최대 원가 차.
        "carryover": _to_jsonable(getattr(res, "carryover_breakdown", None)),
    }

    # Level 2: module_3 가 plan 과 같은 모양의 plan_ids(솔버가 고른 menu_id)를 주면 본식단은 id 로
    # 조회해 솔버·원가·영양·조리지시서가 같은 행을 쓴다. 구버전 module_3(plan_ids 없음)는 대표행(Level 1).
    plan_ids = getattr(res, "plan_ids", None)
    canon = _canonical_by_name(menus)
    nut_by_id = _nutrition_by_id(menus)
    body["plan_ids"] = _to_jsonable(plan_ids) if plan_ids is not None else None

    # 프론트 검토 화면: 메뉴별 열량·단백질·나트륨·원가. id 키(전 후보, 동명 구분)와 이름 키(호환).
    body["menu_nutrition_by_id"] = _to_jsonable(nut_by_id)
    body["menu_nutrition"] = _menu_nutrition_by_name(nut_by_id, canon, plan_ids)

    # 프론트 확정 화면(Step3) 조리 지시서: 재료 투입량(총량)·조리순서.
    # recipe_ingredient_map 미보강 메뉴는 note만 채워져 온다("연동 예정" 대신 실사유 표시 가능).
    if plan_ids is not None:
        recipes_by_id = _build_menu_recipes(cs, plan_ids, payload.serving_count, lambda mid: mid, payload.site_id)
        body["menu_recipes_by_id"] = _to_jsonable(recipes_by_id)
        # 이름 키(호환): 본식단에 오른 행의 레시피. 같은 이름 두 행이 함께 오르면 먼저 나온 행.
        by_name: dict = {}
        for mid, rec in recipes_by_id.items():
            by_name.setdefault(nut_by_id[mid]["name"] if mid in nut_by_id else str(mid), rec)
        body["menu_recipes"] = by_name
    else:
        body["menu_recipes_by_id"] = None
        body["menu_recipes"] = _build_menu_recipes(
            cs, res.plan, payload.serving_count,
            lambda name: canon[name].menu_id if name in canon else None, payload.site_id)

    if payload.with_alternatives and res.plan:
        alts = _derive_alternatives(
            am, res.plan, menus, cfg, payload.allergy_groups, sodium_by_idx, canon
        )
        for a in alts:
            a["plan_ids"] = _alt_plan_ids(a.get("plan"), body["plan"], body["plan_ids"], canon)
        body["alternatives"] = alts
        try:
            body["menu_recipes_by_servings"] = _recipes_by_servings(
                cs, body["plan"], body["plan_ids"], alts, payload.serving_count, payload.site_id)
        except Exception:
            body["menu_recipes_by_servings"] = None  # 없으면 프론트가 인원별로 /recipes 조회(결과 같음)
    body["timing"]["total_sec"] = round(time.monotonic() - started, 2)
    return body


def _servings_need(plan: dict, plan_ids: dict, alts: list[dict], serving_count: int) -> dict[int, set]:
    """조리 인원(전체 인원 제외) → 그 인원으로 만들 menu_id 집합. 프론트 recipeView 와 같은 규칙:
      - 일반식 칸 메뉴: 전체 인원 − 그 끼니에 이 메뉴 대신 대체식을 받는 그룹 인원(그룹 칸에 같은 이름이 없으면 대체)
      - 대체식 칸 메뉴: 일반식 같은 칸에 없는(바뀐) 메뉴만, 그 그룹 인원
    """
    need: dict[int, set] = {}

    def add(servings: int, mid):
        if mid is not None and 0 < servings != serving_count:
            need.setdefault(servings, set()).add(mid)

    groups = [(int((a.get("group") or {}).get("count") or 0), a.get("plan") or {}, a.get("plan_ids") or {}) for a in alts]
    for day, meals in (plan or {}).items():
        for meal, names in (meals or {}).items():
            ids = ((plan_ids or {}).get(day) or {}).get(meal) or []
            for i, name in enumerate(names):
                sub = 0
                for count, aplan, _ in groups:
                    acell = (aplan.get(day) or {}).get(meal)
                    if acell is not None and count >= 1 and name not in acell:
                        sub += count
                add(serving_count - sub, ids[i] if i < len(ids) else None)
            for count, aplan, aids in groups:
                acell = (aplan.get(day) or {}).get(meal) or []
                acell_ids = (aids.get(day) or {}).get(meal) or []
                for i, name in enumerate(acell):
                    if name not in names and count >= 1:
                        add(count, acell_ids[i] if i < len(acell_ids) else None)
    return need


def _recipes_by_servings(cs, plan, plan_ids, alts, serving_count: int, site_id: int | None) -> dict | None:
    """전체 인원이 아닌 조리 인원별 레시피 {인원: {menu_id: 레시피}} — 대체식 그룹·대체 인원을 뺀 일반식.

    menu_recipes_by_id 는 전체 인원 총량이라, 이게 없으면 프론트가 내려받을 때마다 인원별로 /recipes 를 다시 부른다.
    업장 스케일링 총량은 인원에 비례하지 않아 인원마다 _build_menu_recipes 로 따로 만든다. 교체·삭제로 인원이
    바뀐 메뉴는 여기 없으므로 프론트가 그때 조회한다.
    """
    if plan_ids is None or not alts:
        return None
    out: dict = {}
    for servings, mids in sorted(_servings_need(plan, plan_ids, alts, serving_count).items()):
        recs = _build_menu_recipes(cs, {"1": {"점심": sorted(mids)}}, servings, lambda mid: mid, site_id)
        out[str(servings)] = {str(k): v for k, v in recs.items()}
    return _to_jsonable(out)
