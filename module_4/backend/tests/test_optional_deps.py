"""
test_optional_deps.py
=====================
선택적 의존성(모듈 3 CSP · 영양성분 DB) 부재 시 동작 계약.

요구: 앱은 기동하고 다른 엔드포인트는 정상 동작하며, 해당 엔드포인트만 503 + 구체 사유.
(모듈 3은 팀 repo, 영양성분 DB는 docker PostgreSQL 에 있으므로 이 저장소 단독 실행에서는
 보통 부재한다.)
"""

from __future__ import annotations


def test_menu_generate_reports_missing_module3(client, monkeypatch):
    """모듈 3 경로가 없으면 503 + reason=module_3_not_found."""
    from module_4.backend.src import config

    monkeypatch.setattr(config, "module3_src_path", lambda: None)
    r = client.post("/api/menu/generate", json={"days": 7})
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == "module_3_not_found"


def test_menu_wiring_reaches_solver_when_module3_complete(client):
    """모듈 3이 완비된 저장소에서는 **import 실패로 떨어지지 않아야** 한다.

    2026-08-10 develop 병합 전에는 `csp_solver.py`가 존재하지 않는 `soft_constraints`를
    import해 `module_3_import_failed`가 났다. 병합 결과를 고정하는 회귀 테스트다.
    모듈 3이 없거나 불완비인 환경(01_Claude-code 단독 등)에서는 skip.
    """
    import pytest

    from module_4.backend.src import config

    path = config.module3_src_path()
    required = ["csp_solver.py", "csp_hard_constraints.py", "soft_constraints.py",
                "soft_constraints_diversity.py", "alternative_menu.py"]
    if path is None or not all((path / f).exists() for f in required):
        pytest.skip(f"모듈 3 불완비 — 통합 배선 검증 대상 아님 (path={path})")

    r = client.post("/api/menu/generate", json={"days": 3, "solver_time_limit": 40})
    if r.status_code == 200:
        # 메뉴 후보 DB까지 준비된 환경 — 식단이 실제로 나와야 한다.
        body = r.json()
        assert body["status"] in ("OPTIMAL", "FEASIBLE"), body["status"]
        assert body["plan"], "해가 있는데 plan 이 비어 있다"
        return
    # DB 미구성 환경 — 후보 공급원만 없어야 하고, import 실패는 병합 역행 신호다.
    assert r.status_code == 503
    reason = r.json()["detail"]["reason"]
    assert reason != "module_3_import_failed", (
        "모듈 3 파일은 다 있는데 import 실패 — 병합 누락/역행 의심"
    )
    assert reason == "menu_source_unavailable"


def test_nutrition_search_reports_db_unavailable(client, monkeypatch):
    """영양성분 DB 미기동이면 503 + reason 표기 (앱 자체는 살아 있음)."""
    from module_4.backend.src import config

    monkeypatch.setattr(config, "postgres_url",
                        lambda: "postgresql+psycopg2://x:x@127.0.0.1:1/none")
    r = client.get("/api/nutrition/search", params={"q": "김치"})
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] in ("db_unavailable", "driver_missing")
    # 다른 라우터는 영향 없음
    assert client.get("/health").status_code == 200


def test_openapi_schema_lists_all_routers(client):
    """Swagger(OpenAPI) 문서에 4개 라우터가 모두 노출된다 (FR-12 Swagger 완비)."""
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/calibration/observations" in paths
    assert "/api/calibration/references" in paths
    assert "/api/scaling/predict" in paths
    assert "/api/nutrition/search" in paths
    assert "/api/menu/generate" in paths
