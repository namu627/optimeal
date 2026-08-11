# -*- coding: utf-8 -*-
"""이미 적재된 `nutrition_recipe.menu_category` 를 새 분류 규칙으로 재분류한다.

배경(2026-08-11 영양사 지적):
  구 로더는 `grouping_type` 만 보고 '일품요리' 171종을 통째로 '주식'에 넣었다. 그 결과
  포니언 스프·통삼겹스테이크·가지탕수육이 주식 슬롯에 단독 편성됐다.
  새 규칙(`module_3/src/menu_taxonomy.resolve_menu_category`)은 메뉴명까지 보고
  탄수화물 주식만 '주식'에 남긴다.

동작:
  · **멱등** — 이미 새 규칙과 일치하는 행은 건드리지 않는다.
  · **dry-run 기본** — 이동 전건을 출력만 한다. 실제 반영은 `--apply`.
  · `original_data.grouping_type` 을 근거로 재계산하므로, 원본이 없는 행(식약처 적재분
    등)은 건너뛴다. 근거 없이 카테고리를 바꾸지 않는다.

실행:
  docker exec optimeal_app python scripts/reclassify_menu_categories.py
  docker exec optimeal_app python scripts/reclassify_menu_categories.py --apply

⚠ 데이터 변경이다. `--apply` 전에 출력된 이동 목록을 확인할 것.
"""

import argparse
import collections
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "module_3" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_nutrition_from_recipe_db import get_engine  # noqa: E402
from menu_taxonomy import resolve_menu_category  # noqa: E402

SELECT_SQL = text("""
    SELECT nutrition_id, recipe_name, menu_category,
           original_data->>'grouping_type' AS grouping_type
    FROM nutrition_recipe
    WHERE original_data->>'grouping_type' IS NOT NULL
    ORDER BY nutrition_id
""")

UPDATE_SQL = text("""
    UPDATE nutrition_recipe SET menu_category = :cat WHERE nutrition_id = :nid
""")


def plan_moves(conn) -> list[dict]:
    """재분류가 필요한 행 목록을 만든다(변경 없음)."""
    moves = []
    for r in conn.execute(SELECT_SQL).mappings():
        new = resolve_menu_category(r["grouping_type"], r["recipe_name"])
        if new != r["menu_category"]:
            moves.append({"nid": r["nutrition_id"], "name": r["recipe_name"],
                          "old": r["menu_category"], "new": new})
    return moves


def print_plan(moves: list[dict]) -> None:
    """이동 요약 + 전건을 출력한다. 사람이 검토할 수 있어야 한다."""
    summary = collections.Counter((m["old"], m["new"]) for m in moves)
    print(f"[재분류 대상] {len(moves)}건")
    for (old, new), n in summary.most_common():
        print(f"  {old} → {new}: {n}건")
    for (old, new), _ in summary.most_common():
        print(f"\n--- {old} → {new} ---")
        for m in moves:
            if (m["old"], m["new"]) == (old, new):
                print(f"    {m['name']}")


def main() -> int:
    """재분류 진입점."""
    ap = argparse.ArgumentParser(description="menu_category 재분류(메뉴명 반영)")
    ap.add_argument("--apply", action="store_true", help="실제 UPDATE 수행(기본은 dry-run)")
    args = ap.parse_args()

    engine = get_engine()
    with engine.connect() as conn:
        moves = plan_moves(conn)
    print_plan(moves)

    if not moves:
        print("\n[완료] 이미 새 규칙과 일치 — 변경 없음")
        return 0
    if not args.apply:
        print("\n[dry-run] 반영하지 않고 종료. 실제 반영은 --apply")
        return 0

    with engine.begin() as conn:
        for m in moves:
            conn.execute(UPDATE_SQL, {"cat": m["new"], "nid": m["nid"]})
    print(f"\n[완료] {len(moves)}건 재분류")

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT menu_category, count(*) FROM nutrition_recipe "
            "GROUP BY 1 ORDER BY 2 DESC")).all()
    print("[최종 분포] " + " · ".join(f"{c}:{n}" for c, n in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
