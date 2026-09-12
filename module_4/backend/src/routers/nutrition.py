"""
routers/nutrition.py
====================
영양성분 조회 API (FR-10/FR-12, 모듈 1).

모듈 1 데이터(`nutrition_recipe`, 수만 건)는 PostgreSQL 에만 있고 이 저장소에는 없다.
DB 미기동 환경에서도 앱이 뜨도록 SQLAlchemy import·접속을 요청 시점으로 미루고,
실패하면 503 + 구체 사유를 반환한다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import config

router = APIRouter(prefix="/api/nutrition", tags=["nutrition"])


def _sqlalchemy():
    """sqlalchemy 를 지연 import 한다(미설치 환경에서도 앱이 기동해야 하므로).

    Raises:
        HTTPException(503): 드라이버 미설치.
    """
    try:
        import sqlalchemy
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "driver_missing",
                "message": f"sqlalchemy 미설치: {exc}",
                "hint": "requirements.txt 의 sqlalchemy==2.0.36 이 설치된 환경에서 실행하세요.",
            },
        ) from exc
    return sqlalchemy


def _connect():
    """영양성분 DB 커넥션을 연다.

    Raises:
        HTTPException(503): 드라이버 미설치 또는 DB 접속 실패.
    """
    sa = _sqlalchemy()
    try:
        return sa.create_engine(config.postgres_url()).connect()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "db_unavailable",
                "message": f"영양성분 DB 접속 실패: {exc}",
                "hint": "docker-compose up -d db 후 재시도 (POSTGRES_* 환경변수 확인)",
            },
        ) from exc


@router.get("/search", summary="영양성분 검색 (메뉴명)")
def search(
    q: str = Query(..., min_length=1, description="메뉴명 검색어"),
    menu_category: str = Query("", description="주식/국/찌개/주찬/부찬/김치 등"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """메뉴명 부분일치로 영양성분을 검색한다.

    Raises:
        HTTPException(503): 영양성분 DB 미구성.
    """
    text = _sqlalchemy().text
    sql = (
        "SELECT nutrition_id, recipe_name, menu_category, calories, protein, fat, carbs, sodium "
        "FROM nutrition_recipe WHERE recipe_name ILIKE :q "
        + ("AND menu_category = :cat " if menu_category else "")
        + "ORDER BY recipe_name LIMIT :lim"
    )
    params = {"q": f"%{q}%", "lim": limit}
    if menu_category:
        params["cat"] = menu_category
    with _connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return {"count": len(rows), "results": [dict(r) for r in rows]}


@router.get("/{nutrition_id}", summary="영양성분 상세 조회")
def detail(nutrition_id: int) -> dict:
    """영양성분 1건을 조회한다.

    Raises:
        HTTPException(404): 미존재 id.
        HTTPException(503): 영양성분 DB 미구성.
    """
    text = _sqlalchemy().text
    with _connect() as conn:
        row = conn.execute(
            text("SELECT * FROM nutrition_recipe WHERE nutrition_id = :i"),
            {"i": nutrition_id},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"영양성분 없음: {nutrition_id}")
    return dict(row)
