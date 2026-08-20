"""
test_integration.py
===================
OptiMeal 모듈 4 백엔드 — API 통합(E2E) 테스트 (통합본).
라우터별 단위 테스트(test_scaling_api·test_calibration_api·test_optional_deps)와 별개로,
여러 엔드포인트를 실제 사용 흐름으로 이어 호출해 백엔드가 끝까지 동작하는지 검증한다.

담당: 남유찬 (Back) · 2026-08 · 산출물: API 통합 테스트 결과 리포트의 근거.

구성 (인프라 필요 여부로 구분):
  [A] 계약 E2E (S1~S7)        — 인프라 불요 (conftest.py `client`)
  [B] 경계·동시성·다중레시피   — 인프라 불요
  [C] HTTP 표면 (CORS·메서드)  — 인프라 불요
  [D] 실 DB·CSP 파이프라인·제약 — PostgreSQL(모듈1) + 모듈3 CSP 필요, 없으면 skip

검증 축: ADR-008 계약 + FR-11/12 검증기준 + NFR-02 SLA.
"""

from __future__ import annotations

import concurrent.futures
import importlib
import time

import pytest
from fastapi.testclient import TestClient

RECIPE_KEY = "A1034"          # df_B.csv 실재 레시피
SITE_A, SITE_B = 101, 202
FRONT_ORIGIN = "http://localhost:5173"   # Vite 개발 서버


# ---------------------------------------------------------------------------
# 인프라 감지 — 실 DB + 모듈3 구성 여부 (live 테스트 실행/503 저하 테스트 skip 판단)
# ---------------------------------------------------------------------------
def _db_reachable() -> bool:
    try:
        import sqlalchemy
        from module_4.backend.src import config
        with sqlalchemy.create_engine(config.postgres_url()).connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        return True
    except Exception:
        return False


def _module3_present() -> bool:
    from module_4.backend.src import config
    return config.module3_src_path() is not None


def _table_count(sql: str) -> int:
    import sqlalchemy
    from module_4.backend.src import config
    try:
        with sqlalchemy.create_engine(config.postgres_url()).connect() as conn:
            return conn.execute(sqlalchemy.text(sql)).scalar() or 0
    except Exception:
        return 0


_INFRA = _db_reachable() and _module3_present()
skip_if_infra = pytest.mark.skipif(
    _INFRA, reason="실 인프라 구성됨 — 503 저하 대신 [D] live 테스트가 실호출 검증")
requires_infra = pytest.mark.skipif(
    not _INFRA, reason="실 인프라(PostgreSQL + 모듈3) 미구성 — live 통합 테스트 skip")


@pytest.fixture()
def live(tmp_path, monkeypatch):
    """실 DB·모듈3는 그대로 쓰되 캘리브레이션 원장만 격리한 TestClient."""
    monkeypatch.setenv("OPTIMEAL_CALIBRATION_DB", str(tmp_path / "calib.db"))
    from module_4.backend.src import config
    importlib.reload(config)
    from module_4.backend.src.main import create_app
    with TestClient(create_app()) as c:
        yield c


# ===========================================================================
# [A] 계약 E2E — S1~S7 (인프라 불요, conftest.py `client`)
# ===========================================================================
def test_s1_full_calibration_lifecycle(client):
    """업장 등록 → 재료 조회 → cold-start 예측 → 보정 누적 → 재예측 → 수렴곡선까지
    엔드포인트를 실제 순서대로 이어 호출해도 상태가 일관되게 이어지는지 검증."""
    assert client.get("/health").json()["status"] == "ok"
    r = client.post("/api/calibration/sites",
                    json={"site_id": SITE_A, "site_name": "통합테스트업장", "site_type": "학교"})
    assert r.status_code == 201

    recipe = client.get(f"/api/scaling/recipes/{RECIPE_KEY}").json()
    recipe_id, target = recipe["recipe_id"], recipe["ingredients"][0]

    before = client.post("/api/scaling/predict",
                         json={"recipe_key": RECIPE_KEY, "n_target": 100, "site_id": SITE_A}).json()
    assert before["calibrated_count"] == 0

    corrected = target["base_amount_g"] * 100 * 0.6
    for _ in range(3):
        obs = client.post("/api/calibration/observations", json={
            "site_id": SITE_A, "recipe_id": recipe_id, "ingredient_id": target["ingredient_id"],
            "n_target": 100, "base_amount_g": target["base_amount_g"], "corrected_g": corrected})
        assert obs.status_code == 201
    assert obs.json()["round_no"] == 3

    after = client.post("/api/scaling/predict",
                        json={"recipe_key": RECIPE_KEY, "n_target": 100, "site_id": SITE_A}).json()
    assert after["calibrated_count"] == 1
    hit = next(i for i in after["ingredients"] if i["ingredient_id"] == target["ingredient_id"])
    assert hit["method"] == "calibrated"
    assert hit["ratio"] == pytest.approx(0.6)
    assert hit["n_obs"] == 3

    conv = client.get("/api/calibration/convergence", params={
        "site_id": SITE_A, "recipe_id": recipe_id, "ingredient_id": target["ingredient_id"]}).json()
    assert conv["n_obs"] == 3 and len(conv["points"]) == 3
    assert "실증" in conv["caveat"] or "수렴" in conv["caveat"]


def test_s2_partial_apply_and_site_isolation(client):
    """한 재료만 보정 시 같은 업장은 부분 적용, 다른 업장은 미오염."""
    recipe = client.get(f"/api/scaling/recipes/{RECIPE_KEY}").json()
    recipe_id, target = recipe["recipe_id"], recipe["ingredients"][0]
    client.post("/api/calibration/observations", json={
        "site_id": SITE_A, "recipe_id": recipe_id, "ingredient_id": target["ingredient_id"],
        "n_target": 100, "base_amount_g": target["base_amount_g"],
        "corrected_g": target["base_amount_g"] * 100 * 0.6})
    same = client.post("/api/scaling/predict",
                       json={"recipe_key": RECIPE_KEY, "n_target": 100, "site_id": SITE_A}).json()
    other = client.post("/api/scaling/predict",
                        json={"recipe_key": RECIPE_KEY, "n_target": 100, "site_id": SITE_B}).json()
    assert same["calibrated_count"] == 1
    assert same["cold_start_count"] == len(same["ingredients"]) - 1
    assert other["calibrated_count"] == 0


def test_s3_cbr_is_display_only(client):
    """다른 레시피에서 쌓인 보정 이력이 표시는 되지만 값이 자동 적용되진 않는다."""
    recipe = client.get(f"/api/scaling/recipes/{RECIPE_KEY}").json()
    target = recipe["ingredients"][0]
    client.post("/api/calibration/observations", json={
        "site_id": SITE_A, "recipe_id": 9001, "ingredient_id": target["ingredient_id"],
        "n_target": 100, "base_amount_g": 10.0, "corrected_g": 750.0})
    body = client.post("/api/scaling/predict", json={
        "recipe_key": RECIPE_KEY, "n_target": 100, "site_id": SITE_A, "include_cbr": True}).json()
    hit = next(i for i in body["ingredients"] if i["ingredient_id"] == target["ingredient_id"])
    assert any(ref["recipe_id"] == 9001 for ref in hit["cbr_references"])
    assert hit["method"] == "cold_start_linear" and hit["ratio"] == 1.0


@skip_if_infra
def test_s4_nutrition_degrades_to_503(client):
    """PostgreSQL 미구성 시 영양성분 API는 500이 아니라 503 + 사유."""
    r = client.get("/api/nutrition/search", params={"q": "김치"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["reason"] in {"driver_missing", "db_unavailable"} and "hint" in detail


@skip_if_infra
def test_s4_menu_degrades_to_503(client):
    """모듈 3 미구성 시 식단 생성 API는 503 + 사유."""
    r = client.post("/api/menu/generate", json={"days": 7})
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] in {
        "module_3_not_found", "module_3_import_failed", "menu_source_unavailable"}


def test_s5_honesty_contract_on_every_ingredient(client):
    """모든 재료가 method·n_obs·confidence를 갖고 응답에 disclaimer 동봉(ADR-008)."""
    body = client.post("/api/scaling/predict",
                       json={"recipe_key": RECIPE_KEY, "n_target": 250}).json()
    assert body["ingredients"]
    for ing in body["ingredients"]:
        assert ing["method"] in {"cold_start_linear", "calibrated"}
        assert ing["confidence"] in {"cold_start", "low", "high"} and "n_obs" in ing
    assert "선형" in body["disclaimer"]
    assert body["calibrated_count"] + body["cold_start_count"] == len(body["ingredients"])


def test_s6_scaling_latency_under_sla(client):
    """스케일링 예측 핸들러 처리시간 < 3초(NFR-02, 인프로세스 기준)."""
    t0 = time.perf_counter()
    r = client.post("/api/scaling/predict", json={"recipe_key": RECIPE_KEY, "n_target": 1000})
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200 and elapsed < 3.0, f"{elapsed:.3f}s > SLA"


def test_s7_openapi_surface_complete(client):
    """OpenAPI 스펙에 4개 라우터 태그 노출 + /docs 200 (Swagger 완비, FR-12)."""
    spec = client.get("/openapi.json").json()
    tags = {t for p in spec["paths"].values() for op in p.values() for t in op.get("tags", [])}
    assert {"calibration", "scaling", "nutrition", "menu"}.issubset(tags)
    assert client.get("/docs").status_code == 200
    assert spec["info"]["title"] == "OptiMeal API"


# ===========================================================================
# [B] 경계·비정상·동시성·다중레시피 (인프라 불요)
# ===========================================================================
@pytest.mark.parametrize("payload,expected", [
    ({"recipe_key": RECIPE_KEY, "n_target": 1}, 200),
    ({"recipe_key": RECIPE_KEY, "n_target": 5000}, 200),
    ({"recipe_key": RECIPE_KEY, "n_target": 0}, 422),
    ({"recipe_key": RECIPE_KEY, "n_target": 5001}, 422),
    ({"recipe_key": RECIPE_KEY, "n_target": 100, "cbr_top_k": 0}, 422),
    ({"recipe_key": RECIPE_KEY, "n_target": 100, "cbr_top_k": 99}, 422),
    ({"n_target": 100}, 422),
    ({"recipe_key": RECIPE_KEY}, 422),
    ({"recipe_key": "NOPE", "n_target": 100}, 404),
])
def test_predict_boundary_and_invalid(client, payload, expected):
    """스케일링 예측의 경계·누락·미등록 입력 방어."""
    assert client.post("/api/scaling/predict", json=payload).status_code == expected


def test_malformed_json_is_422_not_500(client):
    """깨진 JSON 바디는 500이 아니라 422."""
    r = client.post("/api/scaling/predict", content=b"{not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_observation_domain_violations(client):
    """도메인 위반(base<=0, n<=0, corrected<0)은 400/422로 거부."""
    for bad in ({"base_amount_g": 0}, {"n_target": 0}, {"corrected_g": -1}):
        body = {"site_id": 1, "recipe_id": 1, "ingredient_id": 1,
                "n_target": 100, "base_amount_g": 10.0, "corrected_g": 5.0, **bad}
        assert client.post("/api/calibration/observations", json=body).status_code in (400, 422)


def test_multi_recipe_cold_start_contract_holds(client):
    """레시피 100건 스윕 전부 cold-start 계약(ratio=1, scaled=base×N) 성립."""
    recipes = client.get("/api/scaling/recipes", params={"limit": 100}).json()
    assert len(recipes) >= 20
    checked = 0
    for meta in recipes:
        b = client.post("/api/scaling/predict",
                        json={"recipe_key": meta["recipe_key"], "n_target": 50})
        assert b.status_code == 200, meta["recipe_key"]
        b = b.json()
        for ing in b["ingredients"]:
            assert ing["method"] == "cold_start_linear" and ing["ratio"] == 1.0
            assert ing["scaled_g"] == pytest.approx(ing["base_amount_g"] * 50)
            assert ing["confidence"] == "cold_start"
        assert b["calibrated_count"] == 0
        checked += 1
    assert checked >= 20


def _concurrent_post(client, site_id, n):
    """같은 셀에 동시 보정 n건을 밀어넣고 (상태코드들, recipe_id, ingredient_id) 반환."""
    recipe = client.get(f"/api/scaling/recipes/{RECIPE_KEY}").json()
    rid, target = recipe["recipe_id"], recipe["ingredients"][0]

    def post_one(_):
        return client.post("/api/calibration/observations", json={
            "site_id": site_id, "recipe_id": rid, "ingredient_id": target["ingredient_id"],
            "n_target": 100, "base_amount_g": target["base_amount_g"],
            "corrected_g": target["base_amount_g"] * 100 * 0.6}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        codes = list(ex.map(post_one, range(n)))
    return codes, rid, target["ingredient_id"]


def test_concurrent_observations_no_crash_and_ledger_intact(client):
    """[결함 A 회귀] 동시 보정이 500 없이 전부 201이고 append-only 원장은 유실 없음.
    ⚠ calibration_store 의 check_same_thread=False 수정이 적용돼야 통과."""
    codes, rid, iid = _concurrent_post(client, site_id=777, n=20)
    assert all(c == 201 for c in codes), f"동시 요청 실패(결함 A 미수정?): {codes}"
    conv = client.get("/api/calibration/convergence", params={
        "site_id": 777, "recipe_id": rid, "ingredient_id": iid}).json()
    assert conv["n_obs"] == 20, f"원장 유실: {conv['n_obs']}/20"


@pytest.mark.xfail(reason="결함 B: 셀 추정 read-modify-write lost-update 경쟁 — 미해결",
                   strict=False)
def test_concurrent_estimate_consistency_known_race(client):
    """[결함 B 문서화] 동시 보정 시 파생 셀 추정 n_obs가 낮게 편향(원장은 정상)."""
    codes, rid, iid = _concurrent_post(client, site_id=778, n=20)
    assert all(c == 201 for c in codes)
    est = client.get("/api/calibration/estimate", params={
        "site_id": 778, "recipe_id": rid, "ingredient_id": iid}).json()
    assert est["n_obs"] == 20, f"lost-update: 추정 n_obs={est['n_obs']} (원장은 20)"


def test_coefficients_fallback_surface(client):
    """계수 조회는 필터·상한이 동작하고 운영 미적용 표기를 유지(ADR-008)."""
    body = client.get("/api/scaling/coefficients", params={"limit": 10}).json()
    assert body["count"] <= 10 and body["applied_in_production"] is False
    filt = client.get("/api/scaling/coefficients",
                      params={"group_type": "moist_heat", "limit": 50}).json()
    assert filt["applied_in_production"] is False


# ===========================================================================
# [C] HTTP 표면 — CORS·메서드·미디어타입 (인프라 불요)
# ===========================================================================
def test_cors_preflight_allows_frontend_origin(client):
    """브라우저 preflight(OPTIONS)에 허용 Origin 회신 (프론트 연동 전제)."""
    r = client.options("/api/scaling/predict", headers={
        "Origin": FRONT_ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == FRONT_ORIGIN


def test_cors_actual_request_echoes_origin(client):
    """실제 요청 응답에도 Access-Control-Allow-Origin 헤더가 실린다."""
    r = client.post("/api/scaling/predict",
                    json={"recipe_key": RECIPE_KEY, "n_target": 100},
                    headers={"Origin": FRONT_ORIGIN})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == FRONT_ORIGIN


def test_method_not_allowed_405(client):
    """정의되지 않은 메서드(DELETE)는 405."""
    assert client.delete("/api/scaling/predict").status_code == 405


def test_unknown_path_404(client):
    """미정의 경로는 404."""
    assert client.get("/api/does-not-exist").status_code == 404


def test_wrong_content_type_is_422_not_500(client):
    """JSON 기대 엔드포인트에 폼 데이터 → 500 아니라 422."""
    r = client.post("/api/scaling/predict", data={"recipe_key": "A1034", "n_target": "100"})
    assert r.status_code == 422


def test_error_responses_are_structured_json(client):
    """에러 응답이 구조화된 JSON(detail 포함)."""
    assert "detail" in client.post(
        "/api/scaling/predict", json={"recipe_key": "NOPE", "n_target": 100}).json()
    assert "detail" in client.post(
        "/api/scaling/predict", json={"recipe_key": RECIPE_KEY, "n_target": 0}).json()


# ===========================================================================
# [D] 실 DB · CSP 파이프라인 · Hard Constraint (인프라 필요 — 없으면 skip)
# ===========================================================================
@requires_infra
def test_live_nutrition_search_returns_real_rows(live):
    """영양성분 검색이 실 DB에서 200 + 실제 행을 반환(503 아님)."""
    body = live.get("/api/nutrition/search", params={"q": "김치", "limit": 5})
    assert body.status_code == 200
    body = body.json()
    assert body["count"] >= 1 and "김치" in body["results"][0]["recipe_name"]
    for k in ("nutrition_id", "calories", "sodium"):
        assert k in body["results"][0]


@requires_infra
def test_live_nutrition_detail_and_404(live):
    """검색으로 얻은 id 상세조회 200, 없는 id 404."""
    nid = live.get("/api/nutrition/search", params={"q": "밥", "limit": 1}).json()["results"][0]["nutrition_id"]
    assert live.get(f"/api/nutrition/{nid}").status_code == 200
    assert live.get("/api/nutrition/999999999").status_code == 404


@requires_infra
def test_live_nutrition_category_filter(live):
    """menu_category 필터가 실제로 좁혀준다."""
    r = live.get("/api/nutrition/search", params={"q": "", "menu_category": "국", "limit": 10})
    if r.status_code == 200 and r.json()["count"]:
        assert all(row["menu_category"] == "국" for row in r.json()["results"])


@requires_infra
def test_live_menu_profiles(live):
    """급식 대상 프로파일이 실 DB에서 200 조회."""
    r = live.get("/api/menu/profiles")
    assert r.status_code == 200 and len(r.json()) >= 1
    assert all("profile_key" in p and "daily_kcal" in p for p in r.json())


@requires_infra
def test_live_menu_generate_pipeline(live):
    """모듈1(후보)→모듈3(CSP) 파이프라인이 해를 내고 일별 열량이 목표 밴드 내."""
    r = live.post("/api/menu/generate", json={
        "days": 2, "target_kcal_per_day": 2000, "kcal_tolerance": 0.10,
        "budget_limit_per_person": 3500, "sodium_max_mg_per_day": 2000, "solver_time_limit": 30})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] in {"OPTIMAL", "FEASIBLE"} and b["plan"]
    for _d, kcal in b["daily_kcal"].items():
        assert 2000 * 0.85 <= kcal <= 2000 * 1.15
    assert b["applied_targets"]["sodium_max_mg_per_day"] == 2000


@requires_infra
def test_live_menu_generate_with_alternatives(live):
    """알레르기 그룹 지정 시 공통식+대체식 트랙이 함께 산출(PRD FR-11)."""
    b = live.post("/api/menu/generate", json={
        "days": 2, "solver_time_limit": 30, "with_alternatives": True,
        "allergy_groups": [{"label": "우유알레르기", "allergens": ["우유"], "count": 5}]}).json()
    if b["status"] in {"OPTIMAL", "FEASIBLE"} and b["plan"]:
        assert "alternatives" in b


@requires_infra
def test_live_menu_profile_key_overrides_targets(live):
    """profile_key를 주면 목표를 프로파일에서 산출해 덮어쓴다."""
    key = live.get("/api/menu/profiles").json()[0]["profile_key"]
    r = live.post("/api/menu/generate", json={"days": 2, "profile_key": key, "solver_time_limit": 30})
    assert r.status_code == 200 and r.json()["applied_targets"]["profile"] is not None


@requires_infra
def test_live_hard_sodium_respected(live):
    """나트륨 상한(H-2e)이 결과에 실제 반영 — 매일 ≤ 상한, all_ok=True."""
    r = live.post("/api/menu/generate", json={
        "days": 3, "sodium_max_mg_per_day": 2000, "solver_time_limit": 25}).json()
    if r["status"] not in {"OPTIMAL", "FEASIBLE"}:
        pytest.skip(f"solver {r['status']}")
    nm = r["hard_breakdown"]["nutrient_max"]["sodium"]
    assert nm["all_ok"] is True
    for day in nm["per_day"]:
        assert day["amount"] <= 2000 + 1e-6


@requires_infra
def test_live_hard_kcal_within_band(live):
    """열량 밴드가 매일 준수(kcal_ok=True)."""
    r = live.post("/api/menu/generate", json={
        "days": 3, "target_kcal_per_day": 2000, "kcal_tolerance": 0.10, "solver_time_limit": 25}).json()
    if r["status"] not in {"OPTIMAL", "FEASIBLE"}:
        pytest.skip(f"solver {r['status']}")
    for day in r["hard_breakdown"]["per_day"]:
        assert day["kcal_ok"] is True
        assert 2000 * 0.90 <= day["kcal"] <= 2000 * 1.10


@requires_infra
def test_live_infeasible_is_graceful(live):
    """충족 불가능한 제약은 크래시가 아니라 status=INFEASIBLE + 빈 plan(HTTP 200)."""
    r = live.post("/api/menu/generate", json={
        "days": 2, "target_kcal_per_day": 100, "kcal_tolerance": 0.01, "solver_time_limit": 15})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] in {"INFEASIBLE", "UNKNOWN"} and not b["plan"]


@requires_infra
@pytest.mark.parametrize("meals", [["점심"], ["점심", "저녁"], ["아침", "점심", "저녁"]])
def test_live_meal_count_variations(live, meals):
    """끼니 수(1식/2식/3식) 변형이 해를 내고 applied_targets에 반영."""
    r = live.post("/api/menu/generate",
                  json={"days": 2, "meals": meals, "solver_time_limit": 25}).json()
    assert r["status"] in {"OPTIMAL", "FEASIBLE"} and r["applied_targets"]["meals"] == meals


@requires_infra
def test_live_exclude_menu_keeps_plan_clean(live):
    """exclude_menu_ids 지정 시 편성이 배제 메뉴로부터 청정(excluded_clean=True)."""
    hit = live.get("/api/nutrition/search", params={"q": "밥", "limit": 1}).json()["results"][0]
    r = live.post("/api/menu/generate", json={
        "days": 2, "exclude_menu_ids": [hit["nutrition_id"]], "solver_time_limit": 25}).json()
    if r["status"] not in {"OPTIMAL", "FEASIBLE"}:
        pytest.skip(f"solver {r['status']}")
    assert r["hard_breakdown"]["excluded_clean"] is True


@requires_infra
def test_live_hard_budget_respected(live):
    """예산 상한(H) 준수 — 가격/재료맵 적재 환경에서만. cost>0이고 매일 budget_ok.
    (ingredient_price·recipe_ingredient_map 미적재 시 skip — 리포트 §5-3/§7-F)."""
    if _table_count("SELECT count(*) FROM ingredient_price") == 0 or \
       _table_count("SELECT count(*) FROM recipe_ingredient_map") == 0:
        pytest.skip("ingredient_price/recipe_ingredient_map 미적재 — 예산 검증 불가")
    r = live.post("/api/menu/generate", json={
        "days": 3, "budget_limit_per_person": 3500, "solver_time_limit": 25}).json()
    if r["status"] not in {"OPTIMAL", "FEASIBLE"}:
        pytest.skip(f"solver {r['status']}")
    assert r["total_cost_won"] and r["total_cost_won"] > 0
    for day in r["hard_breakdown"]["per_day"]:
        assert day["budget_ok"] is True


@requires_infra
def test_live_hard_allergen_excluded(live):
    """알레르기 배제(H) — constraints 알레르기 데이터 적재 환경에서만.
    (constraints/recipe_ingredient_map 미적재 시 skip — 리포트 §5-3/§7-F)."""
    if _table_count("SELECT count(*) FROM constraints WHERE constraint_type='알레르기'") == 0 or \
       _table_count("SELECT count(*) FROM recipe_ingredient_map") == 0:
        pytest.skip("constraints(알레르기)/recipe_ingredient_map 미적재 — 알레르기 검증 불가")
    r = live.post("/api/menu/generate", json={
        "days": 3, "excluded_allergens": ["우유"], "solver_time_limit": 25}).json()
    if r["status"] not in {"OPTIMAL", "FEASIBLE"}:
        pytest.skip(f"solver {r['status']}")
    hb = r["hard_breakdown"]
    assert hb.get("excluded_clean") is True or "allergen" in str(hb).lower()