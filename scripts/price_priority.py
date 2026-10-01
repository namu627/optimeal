# -*- coding: utf-8 -*-
"""원가 출처 우선순위 — 재료 하나에 여러 출처 가격이 있을 때 무엇을 쓰는가(2026-09-30 고정).

    KAMIS_W > 가락(서울시농수산식품공사) > KAMIS_R > 참가격_R > 수기_참조 > 파생(쌀 환산·상위품목 대체·동의어 대체) > 정책 0원

- 같은 출처 안에서만 최신 price_date 를 쓴다. 출처가 다르면 날짜와 무관하게 우선순위가 이긴다
  → 로더 실행 순서가 선택 결과를 바꾸지 않는다.
- 목록에 없는 출처(예: 1차 적재의 'KAMIS')는 맨 뒤.
- 같은 목록이 module_3/src/csp_solver.py PRICE_SOURCE_PRIORITY 에도 있다(식단 원가 쿼리).
  두 곳이 같은지는 module_4/backend/tests/test_price_priority.py 가 확인한다.
"""

SOURCE_PRIORITY: tuple = (
    ("KAMIS_W",),
    ("서울시농수산식품공사",),
    ("KAMIS_R",),
    ("참가격_R",),
    ("수기_참조",),
    ("쌀 환산", "상위품목 대체", "동의어 대체"),   # 파생 — 대상 재료가 서로 겹치지 않아 같은 순위
    ("정책 0원",),                               # 물·육수 전용
)


def rank_sql(col: str = "source") -> str:
    """출처 → 순위(작을수록 우선) SQL CASE 식."""
    whens = " ".join(
        f"WHEN {col} IN ({', '.join(repr(s) for s in group)}) THEN {i}"
        for i, group in enumerate(SOURCE_PRIORITY, start=1)
    )
    return f"(CASE {whens} ELSE 99 END)"


def selected_price_sql(where: str = "") -> str:
    """재료별로 고른 가격 한 행씩: ingredient_id, price_per_g, price_date, source. where 는 p 별칭 기준 추가 조건."""
    cond = f"WHERE {where}" if where else ""
    return (f"SELECT DISTINCT ON (p.ingredient_id) p.ingredient_id, p.price_per_g, p.price_date, p.source "
            f"FROM ingredient_price p {cond} "
            f"ORDER BY p.ingredient_id, {rank_sql('p.source')}, p.price_date DESC")
