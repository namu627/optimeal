"""
routers/plans.py
================
저장된 식단(내 식단 목록) API — 모듈 4 전용 MVP.

프론트가 이미 가진 식단 뷰모델(MealPlan JSON)을 **통째로** 저장·조회한다. 다시 열 때
추가 매핑이 필요 없도록 하기 위함이다. 로그인·사용자 체계가 아직 없어 소유자 없이
전역으로 저장한다(누구나 전체 목록을 본다).

저장 테이블은 모듈 4가 스스로 만든다(CREATE TABLE IF NOT EXISTS, 프로세스당 첫 요청 시).
팀 공용 마이그레이션(schema_version)과 충돌하지 않도록 이름에 `m4_` 접두사를 붙였다.
DB 미기동 시 nutrition 라우터와 같은 규약으로 503 + reason·hint 를 반환한다.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from .. import config

router = APIRouter(prefix="/api/menu/plans", tags=["plans"])

TABLE = "m4_saved_meal_plan"
_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    plan        JSONB        NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {TABLE}_created_at_idx ON {TABLE} (created_at DESC);
"""

# 목록용 요약 — plan JSON(프론트 MealPlan)에서 바로 뽑는다(별도 컬럼 동기화 불필요).
_SUMMARY_SQL = """
    id, name, created_at,
    (plan->>'headcount')::int              AS headcount,
    (plan->>'totalDays')::int              AS total_days,
    (plan->>'costPerPerson')::numeric      AS cost_per_person,
    (plan->>'budgetPerPerson')::numeric    AS budget_per_person,
    plan->>'conditionText'                 AS condition_text,
    plan->>'periodText'                    AS period_text,
    plan->'weeks'->0->'days'->0->>'date'   AS start_date
"""

_engine = None
_engine_lock = threading.Lock()
_table_ready = False


class SavePlanRequest(BaseModel):
    """식단 저장 요청."""
    model_config = {"json_schema_extra": {"example": {
        "name": "9/28 · 초등학생 중식", "plan": {"conditionText": "…", "weeks": []}}}}
    name: str = Field(..., min_length=1, max_length=200, description="식단 이름")
    plan: dict = Field(..., description="프론트 MealPlan 뷰모델 JSON(그대로 저장·반환)")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("식단 이름이 비어 있습니다")
        return v

    @field_validator("plan")
    @classmethod
    def _plan_has_weeks(cls, v: dict) -> dict:
        # 최소 형태만 확인한다 — 뷰모델 세부 구조는 프론트 소관이라 서버가 해석하지 않는다.
        if not isinstance(v.get("weeks"), list):
            raise ValueError("plan.weeks(주차 목록)가 없습니다 — MealPlan 뷰모델을 그대로 보내세요")
        return v


def _conn():
    """저장 DB 커넥션(트랜잭션)을 연다. 프로세스당 처음 한 번 테이블을 보장한다.

    Raises:
        HTTPException(503): 드라이버 미설치 또는 DB 접속 실패.
    """
    global _engine, _table_ready
    try:
        import sqlalchemy as sa
    except ImportError as exc:
        raise HTTPException(status_code=503, detail={
            "reason": "driver_missing", "message": f"sqlalchemy 미설치: {exc}",
            "hint": "requirements.txt 의 sqlalchemy==2.0.36 이 설치된 환경에서 실행하세요.",
        }) from exc
    try:
        with _engine_lock:
            if _engine is None:
                _engine = sa.create_engine(config.postgres_url(), pool_pre_ping=True)
            if not _table_ready:
                with _engine.begin() as c:
                    c.execute(sa.text(_DDL))
                _table_ready = True
        return _engine.begin()
    except Exception as exc:
        raise HTTPException(status_code=503, detail={
            "reason": "db_unavailable", "message": f"식단 저장 DB 접속 실패: {exc}",
            "hint": "docker-compose up -d db 후 재시도 (POSTGRES_* 환경변수 확인)",
        }) from exc


def _summary(row) -> dict:
    d = dict(row)
    for k in ("cost_per_person", "budget_per_person"):
        if d[k] is not None:
            d[k] = float(d[k])
    d["created_at"] = _iso(d["created_at"])
    return d


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _not_found(plan_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail={
        "reason": "plan_not_found", "message": f"저장된 식단이 없습니다: id={plan_id}"})


@router.post("", status_code=201, summary="식단 저장")
def save_plan(payload: SavePlanRequest) -> dict:
    """식단 뷰모델을 저장하고 새 id 를 돌려준다."""
    from sqlalchemy import text

    with _conn() as c:
        row = c.execute(
            text(f"INSERT INTO {TABLE} (name, plan) VALUES (:n, CAST(:p AS jsonb)) "
                 "RETURNING id, created_at"),
            {"n": payload.name, "p": json.dumps(payload.plan, ensure_ascii=False)},
        ).mappings().one()
    return {"id": row["id"], "created_at": _iso(row["created_at"])}


@router.get("", summary="저장된 식단 목록 (최신순)")
def list_plans(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    """저장된 식단 요약을 최신순으로 돌려준다(plan 본문 제외)."""
    from sqlalchemy import text

    with _conn() as c:
        rows = c.execute(
            text(f"SELECT {_SUMMARY_SQL} FROM {TABLE} ORDER BY created_at DESC, id DESC LIMIT :lim"),
            {"lim": limit},
        ).mappings().all()
    return [_summary(r) for r in rows]


@router.get("/{plan_id}", summary="저장된 식단 열람")
def get_plan(plan_id: int) -> dict:
    """저장된 식단 한 건(plan 본문 포함).

    Raises:
        HTTPException(404): 없는 id.
    """
    from sqlalchemy import text

    with _conn() as c:
        row = c.execute(
            text(f"SELECT id, name, created_at, plan FROM {TABLE} WHERE id = :id"),
            {"id": plan_id},
        ).mappings().one_or_none()
    if row is None:
        raise _not_found(plan_id)
    return {"id": row["id"], "name": row["name"], "created_at": _iso(row["created_at"]),
            "plan": row["plan"]}


@router.delete("/{plan_id}", status_code=204, summary="저장된 식단 삭제")
def delete_plan(plan_id: int) -> Response:
    """저장된 식단을 삭제한다.

    Raises:
        HTTPException(404): 없는 id.
    """
    from sqlalchemy import text

    with _conn() as c:
        n = c.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": plan_id}).rowcount
    if not n:
        raise _not_found(plan_id)
    return Response(status_code=204)
