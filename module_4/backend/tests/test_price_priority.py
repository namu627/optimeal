"""원가 출처 우선순위가 한 규칙인지 — 식단 원가 쿼리(module_3)와 적재 스크립트(scripts, 파생 부모 가격)가
같은 목록을 쓰는지 확인한다. 한쪽만 바뀌면 파생 가격과 식단 원가가 서로 다른 출처를 고르게 된다.
DB 없이 도는 순수 단위 테스트.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _module3():
    sys.path.insert(0, str(ROOT / "module_3" / "src"))
    import csp_solver
    return csp_solver


def _scripts():
    sys.path.insert(0, str(ROOT / "scripts"))
    import price_priority
    return price_priority


def test_priority_lists_are_identical():
    assert _module3().PRICE_SOURCE_PRIORITY == _scripts().SOURCE_PRIORITY


def test_priority_order_is_the_agreed_one():
    flat = [s for group in _scripts().SOURCE_PRIORITY for s in group]
    assert flat == ["KAMIS_W", "서울시농수산식품공사", "KAMIS_R", "참가격_R", "수기_참조",
                    "쌀 환산", "상위품목 대체", "동의어 대체", "정책 0원"]


def test_menu_query_orders_by_priority_before_date():
    q = _module3()._MENU_QUERY
    head = q[q.index("latest_price"):q.index("allergen")]
    assert "CASE WHEN source IN ('KAMIS_W') THEN 1" in head
    assert head.index("CASE") < head.index("price_date DESC")
    sel = _scripts().selected_price_sql()
    assert sel.index("CASE") < sel.index("price_date DESC")
