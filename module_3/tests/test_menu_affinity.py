# -*- coding: utf-8 -*-
"""메뉴 어울림 Soft 항(B6 Phase 2) 검증 — NEIS 근거표를 목적함수로 옮긴다.

이번 차수의 범위는 사용자 결정(2026-08-15)으로 좁혀졌다:
  · 김치 조건화(H-4d) **미구현** — 8/12 영양사 확정 사양(김치1 고정)을 유지한다.
  · H-4c("찌개·전골·탕은 밥과만") **Hard 유지** — 어울림은 그 위에 병행 추가한다.
따라서 실질 내용은 **한 끼니 안 같은 조리법 중복 회피**다.

여기서 지켜야 할 성질 다섯 가지:
  (1) **점수는 데이터에서 온다** — 표가 없으면 항 전체가 중립(우아한 저하).
  (2) **항이 실제로 일한다** — 끄면 같은 조리법이 실제로 겹친다(음성 대조).
  (3) **희소 축은 안 쓴다** — confidence='low' 행은 기본적으로 계수가 되지 않는다.
  (4) **표 기준선의 상수분이 주식을 벌하지 않는다** — ⑤축 상대 정규화(§B).
  (5) **리포트가 제약이 쓴 라벨을 그대로 읽는다** — 주입 경로가 표에서 사라지면 안 된다.

실행:  pytest module_3/tests/test_menu_affinity.py -v
"""
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

pytest.importorskip("ortools", reason="ortools 미설치")
from ortools.sat.python import cp_model  # noqa: E402

import menu_affinity as ma                               # noqa: E402
from csp_solver import MenuItem                          # noqa: E402


def _rows(*specs) -> list:
    """(축, a, b, 점수, 관측수, 신뢰도) 튜플들로 근거표를 만든다."""
    return [ma.AffinityRow(axis=a, value_a=b, value_b=c, score=s,
                           n_both=n, confidence=conf)
            for a, b, c, s, n, conf in specs]


METHOD = ma.AXIS_METHOD
STAPLE_SOUP = ma.AXIS_STAPLE_SOUP


# --------------------------------------------------------------------------- #
# (A) 근거표 로딩 — 코드가 아니라 데이터                                         #
# --------------------------------------------------------------------------- #
def test_loads_real_table_from_repo():
    """실제 산출된 표(Phase 0, 71행)를 그대로 읽는다."""
    rows = ma.load_affinity_table()
    assert len(rows) >= 60, "Phase 0 근거표를 못 읽었다"
    axes = {r.axis for r in rows}
    assert METHOD in axes and STAPLE_SOUP in axes


def test_missing_table_degrades_to_neutral(tmp_path):
    """표가 없으면 예외가 아니라 빈 목록 — 어울림 때문에 식단 생성이 죽으면 안 된다."""
    assert ma.load_affinity_table(tmp_path / "없는파일.csv") == []


def test_malformed_table_degrades_to_neutral(tmp_path):
    """열이 어긋난 표도 중립으로 떨어진다(운영 중 표가 손상돼도 식단은 나온다)."""
    p = tmp_path / "broken.csv"
    p.write_text("axis,value_a\n조리법×조리법,무침\n", encoding="utf-8")
    assert ma.load_affinity_table(p) == []


def test_low_confidence_rows_are_excluded_by_default():
    """관측 30건 미만 행은 계수가 되지 않는다 — 표본 잡음을 규칙으로 굳히지 않는다."""
    rows = _rows((METHOD, "부침", "부침", -300, 0, "low"),
                 (METHOD, "무침", "무침", -76, 551, "ok"))
    w = ma.AffinityWeights()
    usable = ma._usable(rows, METHOD, w)
    assert [r.value_a for r in usable] == ["무침"]
    assert len(ma._usable(rows, METHOD, ma.AffinityWeights(use_low_confidence=True))) == 2


# --------------------------------------------------------------------------- #
# (B) ⑤축 상대 정규화 — 표 기준선의 상수분이 주식을 벌하면 안 된다                #
# --------------------------------------------------------------------------- #
def test_staple_soup_scores_are_relative_within_staple():
    """면 끼니의 낮은 국 편성률(-108)이 '면 주식 억제'로 새지 않아야 한다.

    표의 기준선에는 '국 없는 끼니'가 섞여 있어, noodle+soup 원점수 -108 은
    "면에 국이 안 어울린다"가 아니라 "면 끼니는 국을 잘 안 낸다"는 뜻이다.
    우리 구성은 국 1개가 필수라 그 상수분을 빼야 한다.
    """
    rows = _rows((STAPLE_SOUP, "noodle", "없음", 136, 411, "ok"),
                 (STAPLE_SOUP, "noodle", "soup", -108, 87, "ok"),
                 (STAPLE_SOUP, "noodle", "stew", -230, 15, "ok"),
                 (STAPLE_SOUP, "rice", "soup", 3, 7385, "ok"),
                 (STAPLE_SOUP, "rice", "stew", 4, 4359, "ok"))
    rel = ma._relative_staple_soup(rows)
    assert ("noodle", "없음") not in rel, "구성상 불가능한 '국 없음'이 남았다"
    assert rel["noodle", "soup"] == 0        # 면에게 가능한 최선이 기준
    assert rel["noodle", "stew"] == -122     # 남는 것은 국 유형 간 상대차뿐
    assert max(rel.values()) == 0            # 전부 ≤ 0 (선호 축을 가점으로 부풀리지 않음)
    assert rel["rice", "soup"] == -1         # 밥은 stew 를 아주 약간 선호


def test_real_table_staple_soup_contributes_nothing_under_hard_h4c():
    """★현행 설정에서 ⑤축 기여가 0 임을 **명시적으로 고정**한다.

    H-4c 가 Hard 로 남아 비밥류+stew 가 애초에 불가능하고, 남은 rice 의 soup/stew
    상대차는 -1 이라 divisor 10 에서 계수가 0 이 된다. 즉 이번 차수의 ⑤축은
    "구현했으나 기여 0"이며, 이는 숨길 게 아니라 기록할 사실이다.
    H-4c 를 Soft 로 완화하면 이 테스트가 깨지고 그때 이 항이 자리를 대신한다.
    """
    w = ma.AffinityWeights()
    rel = ma._relative_staple_soup(ma._usable(ma.load_affinity_table(), STAPLE_SOUP, w))
    coefs = {k: ma._coef(v, w.w_staple_soup, w.score_divisor) for k, v in rel.items()}
    assert set(coefs.values()) == {0}, f"⑤축 계수가 생겼다 — 재측정 필요: {coefs}"


# --------------------------------------------------------------------------- #
# (C) 목적함수 — 조리법 중복 감점이 실제로 걸리는가                              #
# --------------------------------------------------------------------------- #
def _meal_model(menus, *, rows, per_meal=2, weights=None, cooking_methods=None,
                reward=False):
    """한 끼니에서 per_meal 개를 고르는 최소 모델(다른 Soft 항 간섭 없음).

    reward=True 면 season_score 를 작은 가점으로 함께 최대화한다. 음성 대조에서
    **끄면 겹칠 이유**를 만들기 위한 것이다 — 유인 없이 우연히 겹친 결과로 항의
    성과를 주장하면 가짜 테스트가 된다.
    """
    model = cp_model.CpModel()
    Mr = range(len(menus))
    x = {(m, 0, 0): model.NewBoolVar(f"x_{m}") for m in Mr}
    model.Add(sum(x[m, 0, 0] for m in Mr) == per_meal)
    aff = ma.add_affinity_soft_objective(
        model, x, menus, days=1, n_meals=1, table=rows, weights=weights,
        cooking_methods=cooking_methods)
    bonus = 0
    if reward:
        bonus = sum(int(round((menus[m].season_score or 0) * 10)) * x[m, 0, 0] for m in Mr)
    model.Maximize(aff.score + bonus)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    solver.parameters.random_seed = 42
    status = solver.Solve(model)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    return solver, x, aff


def solve_value_keys(aff) -> set:
    """생성된 중복 지시변수의 조리법 라벨 집합."""
    return {g for (_d, _s, g) in aff.method_dup_vars}


def _sides(*names):
    return [MenuItem(i + 1, n, "부찬", 50.0) for i, n in enumerate(names)]


def test_empty_table_makes_every_term_inactive():
    """표를 주지 않으면 변수도 점수도 생기지 않는다 — 8/14 이전과 동일 동작."""
    _solver, _x, aff = _meal_model(_sides("가지무침", "감자조림"), rows=[])
    assert aff.score == 0
    assert not any(aff.active_terms.values())
    assert aff.method_dup_vars == {}


def test_duplicate_method_in_one_meal_is_penalized():
    """같은 조리법 2접시면 감점 지시변수가 1이 된다(회피할 대안이 없을 때)."""
    rows = _rows((METHOD, "무침", "무침", -76, 551, "ok"))
    solver, _x, aff = _meal_model(_sides("가지무침", "오이무침"), rows=rows)
    assert aff.active_terms["method_same"] is True
    assert solver.Value(aff.method_dup_vars[0, 0, "무침"]) == 1
    assert aff.coefficients[METHOD, "무침", "무침"] == -8      # -76/10


def test_indicator_is_zero_when_only_one_dish_of_that_method():
    """접시가 1개면 지시변수는 0 — 양방향 반영이 맞는지 본다(리포트가 이 값을 읽는다)."""
    rows = _rows((METHOD, "무침", "무침", -76, 551, "ok"),
                 (METHOD, "조림", "조림", -92, 47, "ok"))
    solver, _x, aff = _meal_model(_sides("가지무침", "감자조림"), rows=rows)
    assert solver.Value(aff.method_dup_vars[0, 0, "무침"]) == 0
    assert solver.Value(aff.method_dup_vars[0, 0, "조림"]) == 0


def test_negative_control_without_affinity_the_same_method_repeats():
    """★음성 대조 — 항을 끄면 같은 조리법이 실제로 겹친다.

    무침 2종에만 **작은** 제철 가점(0.3 → 3점/접시)을 줘서 끄면 무침으로 쏠릴 이유를
    만든다. 가점을 크게 주면 감점(-8)을 눌러 항이 정상인데도 쏠림이 남으므로
    작게 준다(2026-08-14 주재료 축 테스트와 같은 함정).
    OFF 에서 실제로 겹치지 않으면 픽스처가 쏠림을 못 만든 것이라 그것부터 실패시킨다.
    """
    specs = [("가지무침", 0.3), ("오이무침", 0.3), ("감자조림", 0.0)]
    menus = [MenuItem(i + 1, n, "부찬", 50.0, season_score=s)
             for i, (n, s) in enumerate(specs)]
    rows = _rows((METHOD, "무침", "무침", -76, 551, "ok"))
    labels = ma._scd.classify_cooking_methods(menus)

    def _methods(table):
        solver, x, _aff = _meal_model(menus, rows=table, reward=True)
        return sorted(labels[m] for m in range(len(menus)) if solver.Value(x[m, 0, 0]))

    off, on = _methods([]), _methods(rows)
    assert off == ["무침", "무침"], f"픽스처가 중복을 못 만들었다: {off}"
    assert on == ["무침", "조림"], f"ON 인데 중복이 남았다: {on}"


def test_method_axis_counts_side_dishes_only():
    """조리법 축은 **주찬·부찬만** 본다 — Phase 0 집계 범위와 같아야 점수가 유효하다.

    국(끓이기)·주식까지 세면 표의 기준선과 모집단이 달라져 점수 해석이 무너진다.
    """
    menus = [MenuItem(1, "된장국", "국", 50.0), MenuItem(2, "미역국", "국", 50.0),
             MenuItem(3, "가지무침", "부찬", 50.0)]
    rows = _rows((METHOD, "끓이기", "끓이기", -75, 40, "ok"))
    _solver, _x, aff = _meal_model(menus, rows=rows, per_meal=2)
    assert aff.method_dup_vars == {}, "국까지 조리법 축에 들어갔다"
    assert set(aff.method_by_idx) == {2}


def test_cross_pairs_are_off_by_default_and_opt_in():
    """교차 조합(구이+찜 등)은 Phase 0 판정대로 기본 OFF, 가중치를 줘야 켜진다."""
    menus = _sides("고등어구이", "계란찜")
    rows = _rows((METHOD, "구이", "찜", -83, 120, "ok"))
    _s, _x, off = _meal_model(menus, rows=rows)
    assert off.active_terms["method_cross"] is False
    assert off.method_cross_vars == {}
    _s2, _x2, on = _meal_model(menus, rows=rows,
                               weights=ma.AffinityWeights(w_method_cross=1))
    assert on.active_terms["method_cross"] is True
    assert on.coefficients[METHOD, "구이", "찜"] == -8


def test_kimchi_axis_is_off_by_default():
    """① 김치 축은 기본 OFF — 김치 1개 고정이 유지되는 한 상수항이라 주식만 벌한다."""
    menus = [MenuItem(1, "클럽샌드위치", "주식", 300.0), MenuItem(2, "배추김치", "김치", 10.0)]
    rows = _rows((ma.AXIS_STAPLE_KIMCHI, "bread", "김치있음", -55, 39, "ok"))
    _s, _x, off = _meal_model(menus, rows=rows, per_meal=2)
    assert off.active_terms["staple_kimchi"] is False
    _s2, _x2, on = _meal_model(menus, rows=rows, per_meal=2,
                               weights=ma.AffinityWeights(w_staple_kimchi=1))
    assert on.active_terms["staple_kimchi"] is True
    assert on.coefficients[ma.AXIS_STAPLE_KIMCHI, "bread", "김치있음"] == -6


def test_injected_cooking_methods_win_over_menu_name():
    """DB 권위값 주입이 메뉴명 키워드를 이긴다(다른 모듈과 같은 우선순위)."""
    menus = _sides("가지무침", "오이무침")
    rows = _rows((METHOD, "무침", "무침", -76, 551, "ok"),
                 (METHOD, "볶음", "볶음", -15, 422, "ok"))
    _s, _x, aff = _meal_model(menus, rows=rows, cooking_methods={0: "볶음"})
    assert aff.method_by_idx[0] == "볶음"
    assert solve_value_keys(aff) == {"무침", "볶음"}


# --------------------------------------------------------------------------- #
# (D) 리포트 정합 — 제약이 쓴 라벨을 그대로 보여주는가                            #
# --------------------------------------------------------------------------- #
def test_breakdown_reports_the_labels_the_constraint_used():
    """리포트가 메뉴를 다시 분류하지 않고 제약이 쓴 라벨을 읽는다.

    두 경로가 존재하면 언젠가 갈린다(2026-08-11 나트륨 표·08-14 주재료 표 전례).
    """
    menus = _sides("가지무침", "오이무침")
    rows = _rows((METHOD, "무침", "무침", -76, 551, "ok"))
    solver, x, aff = _meal_model(menus, rows=rows)
    rep = ma.evaluate_affinity_breakdown(solver, x, menus, aff, days=1, n_meals=1)
    assert rep["method_duplicate_count"] == 1
    ev = rep["method_duplicate_meals"][0]
    assert ev["method"] == "무침" and ev["score"] == -8
    assert ev["menus"] == ["가지무침", "오이무침"]
    assert rep["affinity_score"] == -8
    assert rep["method_coverage"] == 2


def test_breakdown_is_empty_when_no_duplicate():
    """중복이 없으면 감점 0 — '항이 켜졌다'와 '감점이 났다'를 구분해 보고한다."""
    menus = _sides("가지무침", "감자조림")
    rows = _rows((METHOD, "무침", "무침", -76, 551, "ok"),
                 (METHOD, "조림", "조림", -92, 47, "ok"))
    solver, x, aff = _meal_model(menus, rows=rows)
    rep = ma.evaluate_affinity_breakdown(solver, x, menus, aff, days=1, n_meals=1)
    assert rep["active_terms"]["method_same"] is True
    assert rep["method_duplicate_count"] == 0
    assert rep["affinity_score"] == 0
