"""
test_calibration_store.py
=========================
캘리브레이션 기판 PoC 검증 (ADR-008 draft §2.2).

검증 계약:
  1. cold-start fallback — 미관측 셀은 선형(ratio=1).
  2. 누적/수렴 — 반복 보정 시 추정이 진실 ratio로 SMA 수렴.
  3. 감사 추적 — 원장 append, 회차 증가, 신뢰도 플래그.
  4. 수렴 곡선 — round 1은 cold-start 대비 오차, 이후 감소.
  5. CBR 참고 — 유사 재료구성 과거 레시피 보정 이력 조회(자동적용 아님).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "calibration"))
from calibration_store import CalibrationStore, _confidence  # noqa: E402


@pytest.fixture
def store():
    s = CalibrationStore(":memory:")
    s.add_site(1, "A초등학교", "학교")
    yield s
    s.close()


def test_cold_start_is_linear(store):
    """미관측 셀 → 선형(ratio=1.0), method=cold_start_linear."""
    p = store.predict(1, 10, 55, base_amount_g=3.0, n_target=100)
    assert p.method == "cold_start_linear"
    assert p.ratio == 1.0
    assert p.scaled_g == pytest.approx(300.0)
    assert p.confidence == "cold_start"


def test_single_observation_remembered(store):
    """1회 보정 후 즉시 그 값을 기억(예측에 반영)."""
    store.record_observation(1, 10, 55, n_target=100, base_amount_g=3.0,
                             corrected_g=420.0)  # ratio=1.4
    p = store.predict(1, 10, 55, base_amount_g=3.0, n_target=100)
    assert p.method == "calibrated"
    assert p.ratio == pytest.approx(1.4)
    assert p.n_obs == 1
    assert p.confidence == "low"


def test_sma_convergence_to_truth(store):
    """반복 보정(진실 ratio≈1.4, 소노이즈)이 SMA로 수렴."""
    truths = [420.0, 405.0, 411.0, 417.0]  # base*N=300 → ratio ~1.35~1.40
    for g in truths:
        store.record_observation(1, 10, 55, 100, 3.0, g)
    p = store.predict(1, 10, 55, 3.0, 100)
    assert p.n_obs == 4
    assert p.confidence == "high"
    # 평균 ratio = mean(g)/300
    expected = (sum(truths) / len(truths)) / 300.0
    assert p.ratio == pytest.approx(expected, rel=1e-6)


def test_ledger_is_append_only_with_rounds(store):
    """원장은 회차별로 누적(감사 추적)."""
    for g in (420.0, 405.0, 411.0):
        store.record_observation(1, 10, 55, 100, 3.0, g)
    rows = store.con.execute(
        "SELECT round_no FROM calibration_observation "
        "WHERE site_id=1 AND recipe_id=10 AND ingredient_id=55 ORDER BY round_no"
    ).fetchall()
    assert [r["round_no"] for r in rows] == [1, 2, 3]


def test_convergence_curve_first_round_vs_coldstart(store):
    """수렴 곡선: round1은 cold-start(ratio=1) 대비 오차, 이후 prior가 실측에 근접."""
    for g in (420.0, 414.0, 417.0):  # ratio ~1.4
        store.record_observation(1, 10, 55, 100, 3.0, g)
    curve = store.convergence_curve(1, 10, 55)
    assert len(curve) == 3
    assert curve[0]["prior_ratio"] == 1.0  # cold-start
    # 마지막 회차의 prior는 누적평균이라 1.0보다 실측(≈1.39)에 훨씬 가까움
    assert curve[-1]["ape_pct"] < curve[0]["ape_pct"]


def test_cbr_references_ranks_by_similarity(store):
    """CBR: 재료구성이 더 유사한 과거 레시피가 먼저. 출처·n_obs 포함."""
    # 과거 레시피 100: 재료 {55(고춧가루),56,57} — 고춧가루 보정 다수
    for g in (420.0, 414.0):
        store.record_observation(1, 100, 55, 100, 3.0, g)
    store.record_observation(1, 100, 56, 100, 5.0, 500.0)
    store.record_observation(1, 100, 57, 100, 2.0, 200.0)
    # 과거 레시피 200: 재료 {55,99} — 고춧가루 보정
    store.record_observation(1, 200, 55, 100, 3.0, 360.0)
    store.record_observation(1, 200, 99, 100, 4.0, 400.0)

    # 신규 레시피 재료 {55,56,57}에 대해 target=55(고춧가루) 참고
    refs = store.cbr_references({55, 56, 57}, target_ingredient_id=55, top_k=3)
    assert len(refs) == 2
    # 레시피100({55,56,57})이 레시피200({55,99})보다 Jaccard 높음 → 먼저
    assert refs[0]["recipe_id"] == 100
    assert refs[0]["jaccard"] >= refs[1]["jaccard"]
    assert "updated_at" in refs[0] and refs[0]["n_obs"] >= 1


def test_confidence_thresholds():
    """신뢰도 플래그 경계."""
    assert _confidence(0) == "cold_start"
    assert _confidence(1) == "low"
    assert _confidence(2) == "low"
    assert _confidence(3) == "high"


def test_invalid_input_raises(store):
    """base 또는 N ≤ 0 → ValueError."""
    with pytest.raises(ValueError):
        store.record_observation(1, 10, 55, 0, 3.0, 420.0)
    with pytest.raises(ValueError):
        store.record_observation(1, 10, 55, 100, 0.0, 420.0)
