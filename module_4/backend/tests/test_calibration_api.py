"""
test_calibration_api.py
=======================
캘리브레이션 API 계약 테스트 (보정 누적 · 셀 추정 · 수렴 곡선 · CBR 참고 표시).

검증 관점은 "ADR-008 계약을 API가 지키는가"이다:
  · 미관측 셀은 cold-start 선형(ratio=1.0)
  · 보정은 ADR-003 단순이동평균으로 누적되고 원장에 append 된다
  · 신뢰도 플래그가 관측 수에 따라 cold_start→low→high 로 승격
  · CBR 응답은 auto_apply=false 를 항상 포함
"""

from __future__ import annotations


def _obs(client, *, site=1, recipe=10, ing=100, n=100, base=10.0, corrected=800.0, **kw):
    """보정 1건 POST 헬퍼."""
    body = {
        "site_id": site, "recipe_id": recipe, "ingredient_id": ing,
        "n_target": n, "base_amount_g": base, "corrected_g": corrected,
    }
    body.update(kw)
    return client.post("/api/calibration/observations", json=body)


def test_health_ok(client):
    """앱은 선택적 의존성(모듈 3·DB) 없이도 기동한다."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["df_b_csv"] is True


def test_estimate_cold_start_is_linear(client):
    """미관측 셀은 ratio=1.0 · method=cold_start_linear · confidence=cold_start."""
    r = client.get("/api/calibration/estimate",
                   params={"site_id": 1, "recipe_id": 10, "ingredient_id": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["est_ratio"] == 1.0
    assert body["method"] == "cold_start_linear"
    assert body["confidence"] == "cold_start"
    assert body["n_obs"] == 0


def test_observation_accumulates_and_updates_estimate(client):
    """보정 적재 → 셀 추정이 관측 ratio 로 갱신되고 제안량이 그에 맞게 나온다."""
    # base 10g × N 100 = 1000g 이 선형 예측. 영양사가 800g 으로 확정 → ratio 0.8
    r = _obs(client, corrected=800.0)
    assert r.status_code == 201
    body = r.json()
    assert body["round_no"] == 1
    assert body["estimate"]["est_ratio"] == 0.8
    assert body["estimate"]["method"] == "calibrated"
    assert body["suggested_g"] == 800.0

    # 조회에도 반영
    e = client.get("/api/calibration/estimate",
                   params={"site_id": 1, "recipe_id": 10, "ingredient_id": 100}).json()
    assert e["est_ratio"] == 0.8
    assert e["n_obs"] == 1


def test_moving_average_over_rounds(client):
    """ADR-003 단순이동평균: ratio 0.8, 1.0 → 평균 0.9."""
    _obs(client, corrected=800.0)
    r = _obs(client, corrected=1000.0)
    assert r.json()["estimate"]["est_ratio"] == 0.9
    assert r.json()["round_no"] == 2


def test_confidence_flag_promotion(client):
    """신뢰도: 0회 cold_start → 1~2회 low → 3회 이상 high."""
    seen = []
    for _ in range(3):
        seen.append(_obs(client, corrected=900.0).json()["estimate"]["confidence"])
    assert seen == ["low", "low", "high"]


def test_observation_rejects_invalid_domain_values(client):
    """base_amount_g ≤ 0 은 pydantic 단계에서 422 로 거부된다."""
    r = _obs(client, base=0.0)
    assert r.status_code == 422


def test_convergence_curve_shape_and_caveat(client):
    """수렴 곡선은 회차별 APE 를 주고, 미실증 caveat 를 항상 동반한다."""
    for c in (800.0, 820.0, 810.0):
        _obs(client, corrected=c)
    r = client.get("/api/calibration/convergence",
                   params={"site_id": 1, "recipe_id": 10, "ingredient_id": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["n_obs"] == 3
    assert [p["round_no"] for p in body["points"]] == [1, 2, 3]
    # 1회차는 cold-start(=1.0) 대비 오차 → headroom
    assert body["points"][0]["prior_ratio"] == 1.0
    # 이후 회차는 이전 추정으로 예측 → 오차가 headroom 보다 작아진다
    assert body["points"][2]["ape_pct"] < body["points"][0]["ape_pct"]
    assert "정상성" in body["caveat"]


def test_cbr_references_are_display_only(client):
    """CBR: 유사 재료구성 과거 레시피 이력을 출처와 함께 표시하되 auto_apply=false."""
    # 과거 레시피 20: 재료 100,101 보정 이력 축적
    _obs(client, recipe=20, ing=100, corrected=700.0)
    _obs(client, recipe=20, ing=101, corrected=500.0)
    # 신규 레시피(재료 100,101,102)에서 재료 100 의 참고 이력 조회
    r = client.get("/api/calibration/references",
                   params={"target_ingredient_id": 100,
                           "ingredient_ids": "100,101,102",
                           "exclude_recipe_id": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["auto_apply"] is False
    assert "자동 적용" in body["disclaimer"]
    assert len(body["references"]) == 1
    ref = body["references"][0]
    assert ref["recipe_id"] == 20 and ref["est_ratio"] == 0.7
    assert 0 < ref["jaccard"] <= 1.0          # 출처·유사도 표기 (닻내림 완화)


def test_cbr_excludes_self_recipe(client):
    """자기 자신(exclude_recipe_id)은 참고 목록에서 제외된다."""
    _obs(client, recipe=20, ing=100, corrected=700.0)
    r = client.get("/api/calibration/references",
                   params={"target_ingredient_id": 100, "ingredient_ids": "100",
                           "exclude_recipe_id": 20})
    assert r.json()["references"] == []


def test_cbr_rejects_malformed_ids(client):
    """ingredient_ids 가 정수 목록이 아니면 400."""
    r = client.get("/api/calibration/references",
                   params={"target_ingredient_id": 100, "ingredient_ids": "1,a,3"})
    assert r.status_code == 400


def test_site_registration(client):
    """업장 등록·조회."""
    assert client.post("/api/calibration/sites",
                       json={"site_id": 7, "site_name": "OO초등학교", "site_type": "학교"}
                       ).status_code == 201
    sites = client.get("/api/calibration/sites").json()
    assert any(s["site_id"] == 7 and s["site_name"] == "OO초등학교" for s in sites)


def test_ledger_is_append_only_across_requests(client):
    """요청당 커넥션이어도 원장이 파일에 누적된다(스레드 안전성 회귀 방지)."""
    for _ in range(4):
        assert _obs(client, corrected=900.0).status_code == 201
    curve = client.get("/api/calibration/convergence",
                       params={"site_id": 1, "recipe_id": 10, "ingredient_id": 100}).json()
    assert curve["n_obs"] == 4
