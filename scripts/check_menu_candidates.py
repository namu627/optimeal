#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: check_menu_candidates.py
목적: DB 세팅 직후 **다중일 식단이 구조적으로 풀릴 만큼** 카테고리별 메뉴 후보가 있는지 점검한다.

왜 필요한가
-----------
메뉴 중복 회피(menu_repeat_window_days=3)는 한 메뉴를 연속 3일에 1회만 허용하고, 창이 하루의
모든 끼니를 포함한다. 그래서 끼니 구성(주식1·국1·주찬1·부찬2~3·김치1)을 채우려면 카테고리마다
최소 **3일 × 끼니 수 × 끼니당 최소 개수** 종이 있어야 한다(3식이면 김치 9종·부찬 18종).
하나라도 모자라면 조건과 무관하게 2일 이상이 INFEASIBLE 이다
(2026-09 김치 1종 사태 — `load_recipe_foodsafety.py` 가 비가열 레시피를 통째로 skip 했었다).

후보는 백엔드 `/api/menu/generate` 와 같은 방식으로 센다:
`csp_solver.load_menus` 와 같은 카테고리 필터 → '반찬' 통합 카테고리를
`menu_taxonomy.classify_side_kind` 로 세분(백엔드 `_reclassify_sides`).
⚠ app 컨테이너에는 ortools 가 없어 `csp_solver` 를 import 하지 않는다 — DB 와 순수 파이썬
  `menu_taxonomy` 만 쓴다. 구성·카테고리 상수는 `csp_solver` 와 같은 값을 유지할 것.

실행:
  docker exec optimeal_app python scripts/check_menu_candidates.py            # 3식 기준
  docker exec optimeal_app python scripts/check_menu_candidates.py --meals 1  # 1식(점심) 기준

종료 코드: 모든 카테고리 충족 0 / 부족 1 (세팅 스크립트·CI 에서 게이트로 쓸 수 있다).
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "module_3" / "src"))

import menu_taxonomy as mt  # noqa: E402
from load_nutrition_from_recipe_db import get_engine  # noqa: E402  (같은 scripts/ 폴더)

# csp_solver.DEFAULT_MENU_CATEGORIES / DEFAULT_COMPOSITION 과 동일 값
MENU_CATEGORIES = ["주식", "국", "찌개", "주찬", "부찬", "김치", "반찬"]
COMPOSITION = {"주식": 1, "국": 1, "주찬": 1, "부찬": (2, 3), "김치": 1}
REPEAT_WINDOW_DAYS = 3   # csp_hard_constraints.HardConstraintConfig.menu_repeat_window_days 기본값


def required_counts(n_meals: int, window: int = REPEAT_WINDOW_DAYS) -> dict:
    """카테고리별 최소 필요 종수 = 창 일수 × 끼니 수 × 끼니당 최소 개수."""
    need = {}
    for cat, spec in COMPOSITION.items():
        lo = spec[0] if isinstance(spec, (tuple, list)) else spec
        need[cat] = window * n_meals * (lo or 0)
    return need


def main() -> int:
    ap = argparse.ArgumentParser(description="카테고리별 메뉴 후보 종수 점검")
    ap.add_argument("--meals", type=int, default=3, choices=(1, 2, 3), help="하루 끼니 수(기본 3)")
    args = ap.parse_args()

    from sqlalchemy import text
    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT recipe_name, menu_category FROM nutrition_recipe "
            "WHERE menu_category = ANY(:c)"), {"c": MENU_CATEGORIES}).all()
    # 백엔드 _reclassify_sides 와 동일(런타임 세분)
    have = Counter(mt.classify_side_kind(name) if cat == "반찬" else cat for name, cat in rows)

    ok = True
    print(f"[후보 점검] {args.meals}식 · 중복 창 {REPEAT_WINDOW_DAYS}일 기준")
    for cat, need in required_counts(args.meals).items():
        mark = "OK" if have[cat] >= need else "부족"
        ok &= have[cat] >= need
        print(f"  {cat:4s} {have[cat]:5d}종 / 최소 {need:3d}종  {mark}")
    if not ok:
        print("[실패] 2일 이상 식단이 INFEASIBLE 이 됩니다 — scripts/README.md 'DB 세팅 순서' 확인")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
