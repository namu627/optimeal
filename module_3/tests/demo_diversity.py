# -*- coding: utf-8 -*-
"""다양성·제철·나트륨당 Soft 목적함수 — 독립 end-to-end 데모.

pmy 골조(`csp_solver.py`)를 **import 하지도, 수정하지도 않는다.** 담당(nyc) 부분만
자체 완결적으로 실행 검증하기 위한 데모다. 실 DB 불필요(내장 mock 메뉴 풀).

실행:  python module_3/demo_diversity.py

증명 대상:
  · 목적함수가 실제 CP-SAT 모델에서 풀린다(end-to-end).
  · 조리법 다양성/제철/나트륨·당 항이 의도대로 식단 선택에 반영된다.
  · 데이터 없는 항(색·주재료·맛)은 크래시 없이 중립 저하한다.
통합(pmy 훅 연결)은 통합 담당자 몫 — 정의서 §6 스니펫 참조.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from ortools.sat.python import cp_model

from soft_constraints_diversity import (
    DiversityWeights,
    add_diversity_soft_objective,
    evaluate_diversity_breakdown,
)


@dataclass
class MenuItem:
    """pmy MenuItem 이 제공할 필드를 모사(데모 자체 정의 — pmy 파일 미참조)."""
    menu_id: int
    name: str
    category: str
    colors: set = field(default_factory=set)
    season_score: float = 0.0
    sodium: float = 0.0
    sugar: float = 0.0
    main_ingredient: str | None = None
    taste: str | None = None
    cooking_method: str | None = None


# 5월(제철: 봄나물류 점수↑) 가정. 조리법·나트륨·당을 다양하게 구성한 mock 풀.
POOL = [
    # 주식
    MenuItem(1, "잡곡밥", "주식", season_score=0.2, sodium=5, sugar=1),
    MenuItem(2, "흰쌀밥", "주식", season_score=0.1, sodium=3, sugar=1),
    # 국물
    MenuItem(3, "냉이된장국", "국", season_score=0.9, sodium=420, sugar=3),
    MenuItem(4, "얼큰소고기국", "국", season_score=0.3, sodium=880, sugar=4),
    # 반찬(조리법 다양)
    MenuItem(5, "제육볶음", "반찬", season_score=0.2, sodium=560, sugar=9),
    MenuItem(6, "고등어구이", "반찬", season_score=0.4, sodium=300, sugar=1),
    MenuItem(7, "감자조림", "반찬", season_score=0.2, sodium=390, sugar=12),
    MenuItem(8, "돈까스", "반찬", season_score=0.1, sodium=640, sugar=6),
    MenuItem(9, "봄나물무침", "반찬", season_score=0.95, sodium=180, sugar=2),
    MenuItem(10, "두부부침", "반찬", season_score=0.2, sodium=210, sugar=1),
    MenuItem(11, "달래양념두부", "반찬", season_score=0.85, sodium=250, sugar=2),
    MenuItem(12, "어묵볶음", "반찬", season_score=0.1, sodium=700, sugar=8),
]


def _enrich(menus: list) -> list:
    """데이터 적재 후(색·주재료) 상황을 모사 — 우아한 저하 항이 켜지는지 확인용."""
    color_map = {"제육볶음": {"red"}, "고등어구이": {"white"}, "감자조림": {"yellow"},
                 "돈까스": {"brown"}, "봄나물무침": {"green"}, "두부부침": {"white"},
                 "달래양념두부": {"green"}, "어묵볶음": {"orange"}, "냉이된장국": {"green"},
                 "얼큰소고기국": {"red"}, "잡곡밥": {"brown"}, "흰쌀밥": {"white"}}
    main_map = {"제육볶음": "돼지고기", "돈까스": "돼지고기", "고등어구이": "고등어",
                "감자조림": "감자", "봄나물무침": "봄나물", "두부부침": "두부",
                "달래양념두부": "두부", "어묵볶음": "어묵", "냉이된장국": "냉이",
                "얼큰소고기국": "소고기", "잡곡밥": "잡곡", "흰쌀밥": "쌀"}
    out = []
    for m in menus:
        e = MenuItem(**{**m.__dict__})
        e.colors = color_map.get(m.name, set())
        e.main_ingredient = main_map.get(m.name)
        out.append(e)
    return out


def run(days: int = 5, menus: list | None = None, label: str = "현재 데이터",
        weights: DiversityWeights | None = None):
    menus = menus if menus is not None else POOL
    weights = weights or DiversityWeights()
    n_meals = 1  # 학교 중식 1끼
    composition = {"주식": 1, "국": 1, "반찬": 2}

    model = cp_model.CpModel()
    M, D, S = range(len(menus)), range(days), range(n_meals)
    x = {(m, d, s): model.NewBoolVar(f"x_{m}_{d}_{s}") for m in M for d in D for s in S}

    # 끼니 슬롯 구성(주식1·국1·반찬2) — pmy 골조와 동일한 하드구조를 데모용으로 재현
    for d in D:
        for s in S:
            for cat, cnt in composition.items():
                model.Add(sum(x[m, d, s] for m in M if menus[m].category == cat) == cnt)
            for m in M:
                if menus[m].category not in composition:
                    model.Add(x[m, d, s] == 0)

    # ★ 담당(nyc) Soft 목적함수 — 이것만 검증 ★
    soft = add_diversity_soft_objective(model, x, menus, days=days, n_meals=n_meals, weights=weights)
    model.Maximize(soft.score)   # 통합 시엔 ksm.score 와 합산

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)

    print("=" * 64)
    print(f"[시나리오: {label}]")
    print(f"풀이 상태: {solver.StatusName(status)}  |  목적값: {solver.ObjectiveValue():.0f}  |  {solver.WallTime():.2f}s")
    print(f"활성 항목: {soft.active_terms}")
    print("=" * 64)
    for d in D:
        picks = [menus[m].name for m in M for s in S if solver.Value(x[m, d, s])]
        print(f"[{d + 1}일차] {', '.join(picks)}")

    rep = evaluate_diversity_breakdown(solver, x, menus, soft, days=days, n_meals=n_meals, weights=weights)
    print("-" * 64)
    print("일별 조리법 종수:")
    for day, info in rep["cooking_diversity"].items():
        mark = "OK" if info["satisfied"] else "미달"
        print(f"  {day}일: {info['distinct']}종 [{mark}] {info['methods']}")
    print(f"총 제철점수(합): {rep['total_season_score']}  (높을수록 좋음)")
    print(f"총 나트륨: {rep['total_sodium_mg']}mg  |  총 당류: {rep['total_sugar_g']}g  (낮을수록 좋음)")
    print(f"색·주재료·맛 활성 여부: color={soft.active_terms['color']}, "
          f"main={soft.active_terms['main']}, taste={soft.active_terms['taste']} (데이터 대기 → 중립)")


if __name__ == "__main__":
    # 1) 현재 데이터 + 기본 가중치: 색·주재료 미적재 & 제철·나트륨 우세 → 반복 식단
    #    (팀장 README '데모 반복' 현상 재현. 완전 무반복은 별도 '메뉴 중복 회피' 항 몫.)
    run(label="현재 데이터 · 기본 가중치")
    print()
    # 2) 데이터 적재(색·주재료) + 다양성 강조 가중치 → 조리법 3종·주재료 분산으로 다양해짐
    #    (가중치 노브가 다양성을 실제로 제어함을 실증. 최종값은 실데이터 캘리브레이션 §6.)
    run(menus=_enrich(POOL), label="데이터 적재 후 · 다양성 강조 가중치",
        weights=DiversityWeights(w_main=25, w_cook=15, w_color=12))
