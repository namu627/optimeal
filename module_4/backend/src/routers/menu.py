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
from dataclasses import asdict, is_dataclass

from fastapi import APIRouter, HTTPException

from .. import config, schemas

router = APIRouter(prefix="/api/menu", tags=["menu"])


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
        return cs.load_menus(month=month)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "menu_source_unavailable",
                "message": f"메뉴 후보 조회 실패: {exc}",
                "hint": "docker-compose 로 PostgreSQL 기동 + nutrition_recipe 적재가 필요합니다.",
            },
        ) from exc


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
    """프로파일 + 끼니 수 → (끼니 이름들, 목표 kcal, 나트륨 상한, 끼니 비율, 근거).

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
        return meals, payload.target_kcal_per_day, payload.sodium_max_mg_per_day, None, None
    import user_profiles as up

    tg = up.targets_for(profile, meals)
    return (meals, tg["target_kcal_per_day"], tg["sodium_max_mg_per_day"],
            tg["meal_energy_ratios"], {"profile": profile.group_name,
                                       "basis": tg["basis"], "source": profile.source,
                                       "note": profile.note})


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


def _derive_alternatives(am, plan, menus, cfg, allergy_groups: list[dict],
                         sodium_by_idx: dict | None = None) -> list[dict]:
    """공통식 plan 에서 알레르기 그룹별 대체식 트랙을 파생한다(PRD FR-11)."""
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
    alts = am.derive_alternative_menus(plan, menus, groups, hard_config=cfg,
                                       sodium_by_id=sodium_by_id)
    return [_to_jsonable(a) for a in alts]


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
    cs, hc, am = _load_module3()
    menus = _load_menu_candidates(cs, payload.month)
    meals, kcal, sodium_max, ratios, basis = _resolve_targets(payload)
    # H-2e 나트륨 상한: 값을 주입할 수 있을 때만 켠다(결측=배제 정책 → 미주입 시 전 메뉴 배제).
    sodium_by_idx = _load_sodium(menus) if sodium_max else None
    cfg = hc.HardConstraintConfig(
        target_kcal_per_day=kcal,
        kcal_tolerance=payload.kcal_tolerance,
        budget_limit_per_person=payload.budget_limit_per_person,
        excluded_allergens=set(payload.excluded_allergens),
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
        solver_time_limit=payload.solver_time_limit,
        hard_nutrient_by_idx=({"sodium": sodium_by_idx} if sodium_by_idx else None),
        main_by_idx=_load_main_ingredients(menus),
        affinity_table=_load_affinity_table(),
    )
    res = cs.build_and_solve(menus, req)
    body = {
        "status": res.status,
        "wall_time_sec": round(res.wall_time, 3),
        # 어떤 기준으로 풀었는지 응답에 남긴다 — 영양사가 화면에서 근거를 볼 수 있어야 한다.
        "applied_targets": {
            "meals": list(meals),
            "target_kcal_per_day": kcal,
            "sodium_max_mg_per_day": sodium_max,
            "profile": basis,
        },
        "plan": _to_jsonable(res.plan),
        "daily_kcal": _to_jsonable(res.daily_kcal),
        "total_cost_won": res.total_cost,
        "hard_breakdown": _to_jsonable(res.hard_breakdown),
        "soft_breakdown": _to_jsonable(res.soft_breakdown),
        "diversity_breakdown": _to_jsonable(res.diversity_breakdown),
        "affinity_breakdown": _to_jsonable(res.affinity_breakdown),
    }

    if payload.with_alternatives and res.plan:
        body["alternatives"] = _derive_alternatives(
            am, res.plan, menus, cfg, payload.allergy_groups, sodium_by_idx
        )
    return body
