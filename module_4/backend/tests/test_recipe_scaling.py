"""
test_recipe_scaling.py
======================
식단 조리 지시서 총량의 기준 표기(basis) 계약 — routers/menu.py `_apply_scaling`.

  · 업장(site_id) 미지정 → 전 재료 '단순 비례'(1인분 × 인원수), base_g = 1인분
  · 업장에 그 (레시피, 재료) 캘리브레이션 추정이 있으면 그 재료만 '스케일링'(est_ratio × 1인분 × 인원수)
실 DB·모듈3 가 필요하다(없으면 skip). 캘리브레이션 원장은 임시 파일로 격리.
"""

from __future__ import annotations

import pytest

from .test_integration import live, requires_infra  # noqa: F401  (live 픽스처 재사용)

NID, KEY, ING = 302530, "A0040", "두부"   # 된장국 — df_B(스케일링 레지스트리)에도 있는 레시피
SERVINGS = 100


def _recipe(client, **params):
    r = client.get("/api/menu/recipes", params={"ids": NID, "servings": SERVINGS, **params})
    assert r.status_code == 200
    rec = r.json()["by_id"].get(str(NID))
    if not rec or not rec["ingredients"]:
        pytest.skip("된장국 레시피 맵이 없는 DB")
    return rec


@requires_infra
def test_without_site_every_amount_is_linear(live):
    rec = _recipe(live)
    for ing in rec["ingredients"]:
        if ing["amount"] is None:
            assert ing["basis"] is None
            continue
        assert ing["basis"] == "단순 비례"
        assert ing["amount"] == pytest.approx(ing["base_g"] * SERVINGS, abs=0.1)


@requires_infra
def test_calibrated_ingredient_is_scaled_others_linear(live):
    from module_4.backend.src import repositories

    rid, iid = repositories.recipe_id_of(KEY), repositories.ingredient_id_of(ING)
    if rid is None or iid is None:
        pytest.skip("스케일링 레지스트리에 된장국·두부가 없음")
    before = {i["name"]: i for i in _recipe(live)["ingredients"]}
    base = before[ING]["base_g"]
    # 영양사가 100인분 두부를 선형(base×100)의 0.8 로 확정 → est_ratio 0.8
    r = live.post("/api/calibration/observations", json={
        "site_id": 1, "recipe_id": rid, "ingredient_id": iid, "n_target": SERVINGS,
        "base_amount_g": base, "corrected_g": base * SERVINGS * 0.8})
    assert r.status_code == 201

    after = {i["name"]: i for i in _recipe(live, site_id=1)["ingredients"]}
    assert after[ING]["basis"] == "스케일링"
    assert after[ING]["amount"] == pytest.approx(base * SERVINGS * 0.8, abs=0.1)
    assert after[ING]["base_g"] == base            # 1인분은 원래 값 — 총량÷인원 이 아님
    others = [i for n, i in after.items() if n != ING and i["amount"] is not None]
    assert others and all(i["basis"] == "단순 비례" for i in others)
    # 다른 업장은 보정이 없다 → 단순 비례
    other_site = {i["name"]: i for i in _recipe(live, site_id=2)["ingredients"]}
    assert other_site[ING]["basis"] == "단순 비례"
