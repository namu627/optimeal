# -*- coding: utf-8 -*-
"""급식 대상 프로파일 + 끼니 수 선택 + 영양사 수동 지정(H-5) 검증 (2026-08-15).

영양사가 자기 업장(학교·복지원·병원·구내식당)을 고르면 그 기준으로 식단이 나와야 한다.
여기서 지켜야 할 성질:
  (1) **수치는 공인 기준에서만 온다** — 2025 한국인 영양소 섭취기준 + 학교급식법 [별표3].
      임의 값을 넣지 않으므로, 표의 값이 출처와 어긋나면 실패시킨다.
  (2) **끼니를 줄이면 목표도 줄어야 한다** — 점심 1식에 하루치 열량을 걸면 한 끼에
      2,000kcal 짜리 식단이 나온다(실제로 그렇게 풀린다).
  (3) **학생은 법정 1식 기준이 우선한다** — 학교급식법이 1식 값을 직접 정해 두었고,
      그 값은 1일의 1/3 수준이라 끼니 비율(점심 40%)과 다르다.
  (4) **수동 지정이 조용히 무시되면 안 된다** — 후보에 없는 id, 추가·제거 동시 지정을
      보고한다.

실행:  pytest module_3/tests/test_user_profiles.py -v
"""
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

pytest.importorskip("ortools", reason="ortools 미설치")
from ortools.sat.python import cp_model  # noqa: E402

import csp_hard_constraints as hc                       # noqa: E402
import user_profiles as up                              # noqa: E402
from csp_solver import MenuItem                         # noqa: E402


# --------------------------------------------------------------------------- #
# (A) 표 로딩 — 공인 기준이 그대로 들어와 있는가                                 #
# --------------------------------------------------------------------------- #
def test_registry_loads_and_covers_requested_groups():
    """요구된 급식 대상 5분류가 모두 있어야 한다(노인·환자·초/중/고·성인·사무직)."""
    profiles = up.load_profiles()
    assert profiles, "프로파일 표를 못 읽었다"
    types = {p.group_type for p in profiles.values()}
    assert {"학생", "일반", "노인", "환자"} <= types
    keys = set(profiles)
    for k in ("elem_low_mix", "elem_high_mix", "middle_mix", "high_mix",
              "univ_mix", "office_mix", "senior_mix", "patient_general_mix"):
        assert k in keys, f"{k} 프로파일이 없다"


@pytest.mark.parametrize("key,kcal,protein,sodium", [
    # 2025 한국인 영양소 섭취기준(보건복지부·한국영양학회) 요약표 그대로.
    ("middle_m", 2500, 60, 2300),      # 남 12-14세
    ("high_m", 2700, 65, 2300),        # 남 15-18세
    ("univ_f", 2000, 55, 2300),        # 여 19-29세
    ("office_m", 2500, 65, 2300),      # 남 30-49세
    ("senior_m", 2000, 60, 1900),      # 남 65-74세 — 나트륨 CDRR 이 성인보다 낮다
    ("senior75_f", 1500, 50, 1800),    # 여 75세 이상
])
def test_values_match_published_standard(key, kcal, protein, sodium):
    """표의 수치가 KDRIs 2025 와 일치한다 — 임의로 바뀌면 실패한다."""
    p = up.load_profiles()[key]
    assert p.daily_kcal == kcal
    assert p.protein_g == protein
    assert p.sodium_cdrr_mg == sodium


@pytest.mark.parametrize("key,meal_kcal", [
    # 학교급식법 시행규칙 [별표3] 학교급식의 영양관리기준 — **1식** 기준.
    ("elem_low_m", 570), ("elem_low_f", 500),
    ("elem_high_m", 670), ("elem_high_f", 600),
    ("middle_m", 840), ("middle_f", 670),
    ("high_m", 900), ("high_f", 670),
])
def test_school_legal_meal_standard(key, meal_kcal):
    """학생 프로파일은 법정 1식 기준을 갖는다."""
    assert up.load_profiles()[key].legal_meal_kcal == meal_kcal


def test_non_students_have_no_legal_meal_standard():
    """성인·노인·환자에는 법정 1식 기준이 없다 — 없는 것을 지어내지 않는다."""
    profiles = up.load_profiles()
    for key in ("univ_mix", "office_mix", "senior_mix", "patient_general_mix"):
        assert profiles[key].legal_meal_kcal is None


def test_derived_rows_are_labelled_as_derived():
    """혼성 행은 파생값(남녀 평균)임이 출처에 드러나야 한다 — 공인값처럼 보이면 안 된다."""
    profiles = up.load_profiles()
    for p in profiles.values():
        if p.sex == "혼성":
            assert "파생" in p.source, f"{p.profile_key} 가 파생 표기가 없다"
        else:
            assert "파생" not in p.source


def test_patient_profile_warns_it_is_not_therapeutic_diet():
    """환자 프로파일은 **일반식**임을 명시해야 한다(치료식 오인은 안전 문제)."""
    for key in ("patient_general_m", "patient_general_f", "patient_general_mix"):
        assert "치료식 아님" in up.load_profiles()[key].note


def test_missing_table_degrades_to_empty(tmp_path):
    """표가 없으면 예외가 아니라 빈 dict — 프로파일 없이도 식단 생성은 돌아야 한다."""
    assert up.load_profiles(tmp_path / "없음.csv") == {}


# --------------------------------------------------------------------------- #
# (B) 끼니 수 → 목표값 산출                                                     #
# --------------------------------------------------------------------------- #
def test_meal_fraction_follows_meal_ratios():
    """점심만 급식하면 하루의 40%, 점심·저녁이면 70%."""
    assert up.meal_fraction(("점심",)) == pytest.approx(0.40)
    assert up.meal_fraction(("점심", "저녁")) == pytest.approx(0.70)
    assert up.meal_fraction(up.DEFAULT_MEALS) == pytest.approx(1.00)


def test_targets_scale_down_with_fewer_meals():
    """★끼니를 줄이면 목표 열량·나트륨이 함께 줄어야 한다.

    안 줄이면 한 끼에 하루치를 우겨넣는 식단이 나온다(실측으로 확인된 동작).
    """
    senior = up.load_profiles()["senior_mix"]        # 1일 1,800kcal · Na 1,900mg
    three = up.targets_for(senior, ("아침", "점심", "저녁"))
    two = up.targets_for(senior, ("점심", "저녁"))
    one = up.targets_for(senior, ("점심",))
    assert three["target_kcal_per_day"] == pytest.approx(1800)
    assert two["target_kcal_per_day"] == pytest.approx(1260)     # 1800 × 0.7
    assert one["target_kcal_per_day"] == pytest.approx(720)      # 1800 × 0.4
    assert two["sodium_max_mg_per_day"] == pytest.approx(1330)   # 1900 × 0.7
    assert one["target_kcal_per_day"] < two["target_kcal_per_day"] < three["target_kcal_per_day"]


def test_student_one_meal_uses_legal_standard_not_ratio():
    """★학생 1식은 법정 기준(840kcal)이지 하루의 40%(1,000kcal)가 아니다."""
    middle = up.load_profiles()["middle_m"]          # 1일 2,500 · 법정 1식 840
    one = up.targets_for(middle, ("점심",))
    assert one["target_kcal_per_day"] == pytest.approx(840)
    assert "[별표3] 1식 기준" in one["basis"]
    # 3식이면 법정 1식 기준이 아니라 1일 기준을 쓴다.
    #   ⚠ basis 에 출처 문자열('KDRIs2025+학교급식법별표3')이 섞이므로 '학교급식법'
    #     단순 포함으로 판별하면 안 된다 — 어느 기준을 **적용했는지**로 본다.
    three = up.targets_for(middle, up.DEFAULT_MEALS)
    assert three["target_kcal_per_day"] == pytest.approx(2500)
    assert "1일 기준" in three["basis"] and "[별표3] 1식 기준" not in three["basis"]


def test_meal_ratios_are_renormalized_within_selected_meals():
    """편성한 끼니들 안에서 비율 합이 1이 되어야 끼니 배분 제약이 켜진다.

    (3식 기본값 0.3/0.4/0.3 을 2식에 그대로 넘기면 개수가 안 맞아 제약이 조용히 꺼진다.)
    """
    senior = up.load_profiles()["senior_mix"]
    r2 = up.targets_for(senior, ("점심", "저녁"))["meal_energy_ratios"]
    assert len(r2) == 2 and sum(r2) == pytest.approx(1.0, abs=1e-3)
    assert r2[0] > r2[1]                       # 점심 40 : 저녁 30 → 0.571 : 0.429
    r1 = up.targets_for(senior, ("점심",))["meal_energy_ratios"]
    assert r1 == (1.0,)


# --------------------------------------------------------------------------- #
# (C) H-5 영양사 수동 지정                                                      #
# --------------------------------------------------------------------------- #
def _manual_model(menus, *, cfg, days=2, n_meals=1):
    """카테고리 구성 없이 슬롯당 1개만 고르는 최소 모델(H-5 만 본다)."""
    model = cp_model.CpModel()
    Mr, D, S = range(len(menus)), range(days), range(n_meals)
    x = {(m, d, s): model.NewBoolVar(f"x_{m}_{d}_{s}") for m in Mr for d in D for s in S}
    for d in D:
        for s in S:
            model.Add(sum(x[m, d, s] for m in Mr) == 1)
    hard = hc.add_hard_constraints(model, x, menus, days=days, n_meals=n_meals, config=cfg)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    solver.parameters.random_seed = 42
    status = solver.Solve(model)
    return solver, x, hard, status


def _sides(n=3):
    return [MenuItem(100 + i, f"메뉴{i}", "부찬", 300.0) for i in range(n)]


def _cfg(**kw):
    """열량 등 다른 Hard 는 끄고 H-5 만 켠 설정."""
    return hc.HardConstraintConfig(enable_energy=False, enable_meal_ratio=False,
                                   budget_limit_per_person=None,
                                   menu_repeat_window_days=0, **kw)


def test_excluded_menu_never_appears():
    """제거한 메뉴는 지평 전체에서 편성되지 않는다."""
    menus = _sides(3)
    solver, x, hard, status = _manual_model(menus, cfg=_cfg(exclude_menu_ids={101}))
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assert all(not solver.Value(x[1, d, 0]) for d in range(2))
    assert hard.active_terms["manual_exclude"] is True


def test_included_menu_is_placed_at_least_once():
    """추가한 메뉴는 지평 안에 최소 1회 편성된다."""
    menus = _sides(3)
    solver, x, hard, _ = _manual_model(menus, cfg=_cfg(include_menu_ids={102}))
    assert sum(int(solver.Value(x[2, d, 0])) for d in range(2)) >= 1
    assert hard.active_terms["manual_include"] is True


def test_exclude_wins_over_include_and_is_reported():
    """같은 메뉴를 추가·제거 동시 지정하면 **배제가 이기고**, 그 사실을 보고한다.

    조용히 한쪽을 고르면 영양사는 반영된 줄 안다.
    """
    menus = _sides(3)
    solver, x, hard, status = _manual_model(
        menus, cfg=_cfg(exclude_menu_ids={101}, include_menu_ids={101}))
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assert all(not solver.Value(x[1, d, 0]) for d in range(2))
    assert hard.conflicting_menu_ids == {101}


def test_unknown_menu_id_is_reported_not_silently_dropped():
    """후보에 없는 id 는 제약이 걸릴 데가 없다 — 보고해야 한다."""
    menus = _sides(3)
    _s, _x, hard, _ = _manual_model(menus, cfg=_cfg(include_menu_ids={999999}))
    assert 999999 in hard.unknown_menu_ids
    assert hard.active_terms["manual_include"] is False


def test_manual_report_flags_incomplete_application():
    """리포트가 '요청분 대비 실제 반영'을 드러낸다."""
    menus = _sides(3)
    solver, x, hard, _ = _manual_model(
        menus, cfg=_cfg(include_menu_ids={102, 999999}))
    rep = hc.evaluate_hard_breakdown(solver, x, menus, hard, days=2, n_meals=1)["manual"]
    assert rep["included_placed_counts"][102] >= 1
    assert rep["unknown_menu_ids"] == [999999]
    assert rep["all_ok"] is False


def test_manual_report_absent_when_unused():
    """수동 지정을 안 쓰면 리포트에 빈 dict — 없는 항목을 만들지 않는다."""
    menus = _sides(3)
    solver, x, hard, _ = _manual_model(menus, cfg=_cfg())
    rep = hc.evaluate_hard_breakdown(solver, x, menus, hard, days=2, n_meals=1)
    assert rep["manual"] == {}
