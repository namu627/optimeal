"""
test_scaling_api.py
===================
스케일링 API 계약 테스트.

ADR-008 계약 검증:
  · site_id 없으면 전 재료 cold-start 선형(scaled = base×N)
  · 보정이 쌓인 재료만 calibrated 로 바뀌고, 나머지는 cold-start 유지(부분 적용)
  · 응답에 재료별 근거(method·n_obs·confidence) + 정직성 고지 동봉
  · 계수 조회는 운영 미적용(applied_in_production=false) 표기
"""

from __future__ import annotations

import pytest

RECIPE_KEY = "A1034"   # df_B.csv 실재 레시피 (마늘·미나리·실파 …)


@pytest.fixture()
def recipe(client):
    """대상 레시피의 메타 + 재료 구성."""
    r = client.get(f"/api/scaling/recipes/{RECIPE_KEY}")
    assert r.status_code == 200
    return r.json()


def test_recipe_listing_exposes_int_ids(client):
    """캘리브레이션 API가 요구하는 정수 id 매핑을 목록에서 제공한다."""
    rows = client.get("/api/scaling/recipes", params={"q": RECIPE_KEY}).json()
    assert rows and rows[0]["recipe_key"] == RECIPE_KEY
    assert isinstance(rows[0]["recipe_id"], int)


def test_unknown_recipe_404(client):
    """미등록 레시피 키는 404."""
    assert client.get("/api/scaling/recipes/NOPE").status_code == 404
    r = client.post("/api/scaling/predict", json={"recipe_key": "NOPE", "n_target": 100})
    assert r.status_code == 404


def test_predict_cold_start_is_pure_linear(client, recipe):
    """site_id 미지정 → 전 재료 base×N, method=cold_start_linear."""
    r = client.post("/api/scaling/predict", json={"recipe_key": RECIPE_KEY, "n_target": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["calibrated_count"] == 0
    assert body["cold_start_count"] == len(body["ingredients"])
    by_id = {i["ingredient_id"]: i for i in body["ingredients"]}
    for ing in recipe["ingredients"]:
        item = by_id[ing["ingredient_id"]]
        assert item["method"] == "cold_start_linear"
        assert item["ratio"] == 1.0
        assert item["scaled_g"] == pytest.approx(ing["base_amount_g"] * 100)
    assert "선형" in body["disclaimer"]


def test_predict_applies_calibration_partially(client, recipe):
    """보정이 있는 재료만 calibrated, 나머지는 cold-start 유지."""
    target = recipe["ingredients"][0]
    recipe_id = recipe["recipe_id"]
    # 이 업장에서 해당 재료를 선형의 60% 로 확정
    client.post("/api/calibration/observations", json={
        "site_id": 5, "recipe_id": recipe_id, "ingredient_id": target["ingredient_id"],
        "n_target": 100, "base_amount_g": target["base_amount_g"],
        "corrected_g": target["base_amount_g"] * 100 * 0.6,
    })
    body = client.post("/api/scaling/predict", json={
        "recipe_key": RECIPE_KEY, "n_target": 100, "site_id": 5,
    }).json()

    assert body["calibrated_count"] == 1
    assert body["cold_start_count"] == len(body["ingredients"]) - 1
    hit = next(i for i in body["ingredients"] if i["ingredient_id"] == target["ingredient_id"])
    assert hit["method"] == "calibrated"
    assert hit["ratio"] == pytest.approx(0.6)
    assert hit["scaled_g"] == pytest.approx(target["base_amount_g"] * 100 * 0.6)
    assert hit["confidence"] == "low"          # 1회 관측
    others = [i for i in body["ingredients"] if i["ingredient_id"] != target["ingredient_id"]]
    assert all(o["method"] == "cold_start_linear" for o in others)


def test_calibration_is_site_scoped(client, recipe):
    """업장 5의 보정이 업장 6의 결과를 오염시키지 않는다."""
    target = recipe["ingredients"][0]
    client.post("/api/calibration/observations", json={
        "site_id": 5, "recipe_id": recipe["recipe_id"],
        "ingredient_id": target["ingredient_id"], "n_target": 100,
        "base_amount_g": target["base_amount_g"],
        "corrected_g": target["base_amount_g"] * 100 * 0.6,
    })
    other = client.post("/api/scaling/predict", json={
        "recipe_key": RECIPE_KEY, "n_target": 100, "site_id": 6,
    }).json()
    assert other["calibrated_count"] == 0


def test_predict_can_attach_cbr_references(client, recipe):
    """include_cbr=true 면 재료별 과거 보정 이력이 표시된다(다른 레시피 출처)."""
    target = recipe["ingredients"][0]
    # 다른 레시피(999)에서 같은 재료 보정 이력 축적
    client.post("/api/calibration/observations", json={
        "site_id": 5, "recipe_id": 999, "ingredient_id": target["ingredient_id"],
        "n_target": 100, "base_amount_g": 10.0, "corrected_g": 750.0,
    })
    body = client.post("/api/scaling/predict", json={
        "recipe_key": RECIPE_KEY, "n_target": 100, "site_id": 5, "include_cbr": True,
    }).json()
    hit = next(i for i in body["ingredients"] if i["ingredient_id"] == target["ingredient_id"])
    assert any(r["recipe_id"] == 999 for r in hit["cbr_references"])
    # 참고를 표시했다고 값이 자동 적용되지는 않는다 → 이 셀은 여전히 cold-start
    assert hit["method"] == "cold_start_linear"
    assert hit["ratio"] == 1.0


def test_coefficients_marked_not_applied(client):
    """계수 조회는 학술 비교군 표기(applied_in_production=false)를 포함한다."""
    body = client.get("/api/scaling/coefficients", params={"limit": 5}).json()
    assert body["applied_in_production"] is False
    assert body["count"] == 5
    assert "ADR-008" in body["note"]
