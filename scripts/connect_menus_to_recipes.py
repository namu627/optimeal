# -*- coding: utf-8 -*-
"""CSP 메뉴(760) ↔ 재료있는 레시피 연결 — 이름 기준으로 recipe.nutrition_recipe_id 세팅.

배경: CSP 후보 메뉴(nutrition_recipe 의 주식/국/반찬 등 760종)가 재료·가격 데이터를 가진
      레시피에 안 이어져 있어 전 메뉴 원가가 0원이었다. 이름 일치율이 99%라 자동 연결한다.

안전장치:
  · 기본은 --dry-run (미리보기, DB 변경 없음). 실제 반영은 --apply.
  · --apply 는 먼저 recipe 의 현재 연결 상태를 백업 테이블로 저장(되돌리기 가능).
  · 메뉴당 레시피 '딱 1개'만 연결(재료 많은 쪽 우선) → 원가 이중 집계 방지.
  · 멱등: 여러 번 돌려도 같은 결과.

실행:
  docker exec optimeal_app python scripts/connect_menus_to_recipes.py            # 미리보기
  docker exec optimeal_app python scripts/connect_menus_to_recipes.py --apply    # 실제 연결
되돌리기: --apply 가 출력한 백업 테이블에서 recipe.nutrition_recipe_id 를 복원하면 된다.
"""
import argparse
import os

CATS = ["주식", "국", "찌개", "주찬", "부찬", "김치", "반찬"]


def get_engine():
    from sqlalchemy import create_engine
    host = os.getenv("POSTGRES_HOST", "localhost"); port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "optimeal"); user = os.getenv("POSTGRES_USER", "optimeal")
    pw = os.getenv("POSTGRES_PASSWORD", "optimeal_dev_pw")
    return create_engine(f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{db}")


# 메뉴당 레시피 1개 선택: 이름 일치 + 재료 보유 레시피 중 '재료 수 최다', 동률이면 최소 recipe_id
CHOSEN_SQL = """
WITH ing AS (
    SELECT recipe_id, COUNT(*) AS ing_count
    FROM recipe_ingredient_map GROUP BY recipe_id
),
cand AS (
    SELECT nr.nutrition_id AS menu_id, nr.recipe_name AS name,
           r.recipe_id, ing.ing_count,
           ROW_NUMBER() OVER (PARTITION BY nr.nutrition_id
                              ORDER BY ing.ing_count DESC, r.recipe_id ASC) AS rn
    FROM nutrition_recipe nr
    JOIN recipe r  ON r.recipe_name = nr.recipe_name
    JOIN ing       ON ing.recipe_id = r.recipe_id
    WHERE nr.menu_category = ANY(:c)
)
SELECT menu_id, name, recipe_id, ing_count FROM cand WHERE rn = 1
"""


def main():
    from sqlalchemy import text
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 연결(미지정 시 미리보기만)")
    args = ap.parse_args()

    eng = get_engine()
    with eng.connect() as c:
        total_menus = c.execute(text(
            "SELECT COUNT(*) FROM nutrition_recipe WHERE menu_category = ANY(:c)"), {"c": CATS}).scalar()
        chosen = c.execute(text(CHOSEN_SQL), {"c": CATS}).all()

    print(f"CSP 메뉴 총 {total_menus}종 · 연결 대상(이름 일치+재료보유) {len(chosen)}종")
    print(f"연결 안 되는 메뉴 {total_menus - len(chosen)}종(이름 일치 레시피 없음 → 원가 0 유지)")
    print("\n── 연결 미리보기(앞 10) ──")
    for menu_id, name, rid, ic in chosen[:10]:
        print(f"  메뉴 {menu_id:>7} '{name}'  ←  recipe {rid} (재료 {ic}개)")

    if not args.apply:
        print("\n[미리보기] DB 변경 없음. 실제 반영은 --apply 를 붙여 실행.")
        return

    from sqlalchemy import text
    with eng.begin() as conn:
        ts = conn.execute(text("SELECT to_char(now(),'YYYYMMDDHH24MISS')")).scalar()
        backup = f"recipe_nrid_backup_{ts}"
        conn.execute(text(f"CREATE TABLE {backup} AS "
                          f"SELECT recipe_id, nutrition_recipe_id FROM recipe"))
        # 대상 메뉴에 이미 붙은 링크를 먼저 해제(중복/재실행 안전) → 메뉴당 1개 보장
        menu_ids = [row[0] for row in chosen]
        conn.execute(text("UPDATE recipe SET nutrition_recipe_id = NULL "
                          "WHERE nutrition_recipe_id = ANY(:ids)"), {"ids": menu_ids})
        # 선택된 레시피만 해당 메뉴로 연결
        n = 0
        for menu_id, name, rid, ic in chosen:
            conn.execute(text("UPDATE recipe SET nutrition_recipe_id = :mid "
                              "WHERE recipe_id = :rid"), {"mid": menu_id, "rid": rid})
            n += 1

    print(f"\n[완료] {n}종 메뉴를 레시피에 연결. 백업 테이블: {backup}")
    print("  되돌리려면 이 백업으로 recipe.nutrition_recipe_id 를 복원하면 됩니다.")
    print("\n다음: 원가 반영 확인(상한·하한 동작)")
    print("  docker exec optimeal_app python module_3/src/demo_budget.py --cap 4000 --floor 1500")


if __name__ == "__main__":
    main()
