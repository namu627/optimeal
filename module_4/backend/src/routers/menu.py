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
    완전히 정확하려면 plan 에 menu_id 가 실려 솔버가 고른 행을 그대로 써야 하며, 이는 module_3
    (csp_solver·alternative_menu) 소관의 Level 2 수정이다.
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


def _load_menu_nutrition(canon: dict) -> dict:
    """메뉴명 -> {kcal, protein, sodium, cost}. 프론트 검토 화면의 셀별 표기용(FR-검토).
    calories·cost 는 후보(MenuItem)에 이미 있고, protein·sodium 은 nutrition_recipe 에서 보강한다.
    조회에 실패해도 kcal·cost 는 채우고 protein·sodium 만 None 으로 둔다 — 응답은 항상 나간다.

    Args:
        canon: _canonical_by_name 결과(메뉴명 -> 대표행). 동명 메뉴는 대표행 값만 싣는다.
    """
    menus = list(canon.values())
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
        out[m.name] = {
            "kcal": round(float(getattr(m, "calories", 0) or 0), 1),
            "protein": prot.get(getattr(m, "menu_id", None)),
            "sodium": sod.get(getattr(m, "menu_id", None)),
            "cost": round(float(getattr(m, "cost_won", 0) or 0)),
        }
    return out


def _make_recipe_of_by_id(engine, canon: dict):
    """메뉴명 -> 대표행(menu_id)의 레시피를 조회하는 recipe_of (cooking_sheet 주입용).

    module_3 의 cooking_sheet.make_db_recipe_of 는 `WHERE nr.recipe_name = :menu` 로 조회해
    동명 행이 여럿이면 모든 행의 재료를 섞을 수 있다. 여기서는 대표행 nutrition_id 로만 조회해
    menu_nutrition·대체식과 같은 행을 쓴다. 반환 형식은 make_db_recipe_of 와 동일하다.
    대표행이 없는 이름(후보 밖 메뉴)은 None → 조리 지시서에 '레시피 없음'으로 남는다.
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

    def recipe_of(menu_name):
        m = canon.get(menu_name)
        if m is None:
            return None
        with engine.connect() as conn:
            rows = conn.execute(query, {"nid": m.menu_id}).mappings().all()
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


def _build_menu_recipes(cs, plan: dict, servings: int, canon: dict) -> dict:
    """확정 plan에 등장하는 메뉴별 재료 투입량(총량)·조리순서(Step3 조리 지시서용).

    module_3.cooking_sheet.build_cooking_sheet 는 day/meal/menu 단위로 행을 내지만,
    재료 구성은 메뉴명에만 의존하므로 여기서 메뉴명 키로 한 번만 접어 돌려준다
    (프론트가 날짜와 무관하게 메뉴명으로 조회할 수 있게).
    레시피는 동명 정규화된 대표행(canon) 기준으로 조회한다(_make_recipe_of_by_id).
    레시피(recipe_ingredient_map)가 없는 메뉴는 note만 채운 항목으로 남긴다.
    실패해도(DB 미구성 등) 조리 지시서 없이 응답은 나가야 하므로 빈 dict로 저하한다.
    """
    if not plan or not servings:
        return {}
    try:
        import cooking_sheet as csheet

        recipe_of = _make_recipe_of_by_id(cs.get_engine(), canon)
        rows = csheet.build_cooking_sheet(plan, recipe_of, servings)
    except Exception:
        return {}
    out: dict = {}
    for r in rows:
        out.setdefault(r["menu"], {
            "cooking_method": r["cooking_method"],
            "ingredients": r["ingredients"],
            "note": r["note"],
        })
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
    alts = am.derive_alternative_menus(plan, alt_menus, groups, hard_config=cfg,
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
    meals, kcal, sodium_max, ratios, basis, protein_g = _resolve_targets(payload)
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
            "protein_g": protein_g,  # 프로파일 기준 단백질 목표(제약 아님, 표시용). 프로파일 없으면 None
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

    # plan 은 메뉴명만 담는다 → 이름으로 행을 다시 찾는 아래 세 곳이 같은 행(대표행)을 쓰도록 정규화.
    canon = _canonical_by_name(menus)

    # 프론트 검토 화면: plan 은 메뉴명만 담으므로, 메뉴별 열량·단백질·나트륨·원가를 옆에 실어준다.
    body["menu_nutrition"] = _load_menu_nutrition(canon)

    # 프론트 확정 화면(Step3) 조리 지시서: 메뉴명 → 재료 투입량(총량)·조리순서.
    # recipe_ingredient_map 미보강 메뉴는 note만 채워져 온다("연동 예정" 대신 실사유 표시 가능).
    body["menu_recipes"] = _build_menu_recipes(cs, res.plan, payload.serving_count, canon)

    if payload.with_alternatives and res.plan:
        body["alternatives"] = _derive_alternatives(
            am, res.plan, menus, cfg, payload.allergy_groups, sodium_by_idx, canon
        )
    return body
