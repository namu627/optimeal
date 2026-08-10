"""
calibration_store.py
====================
캘리브레이션 기판 PoC (ADR-008 draft §2.2).

예측이 현 데이터로 불가능함이 실측 검증된 도메인에서, 영양사 보정을
**감사 가능하게 누적·기억**하여 반복 사용 시 그 업장 현실로 수렴시키는 저장소.

핵심 설계:
  - cold-start = 선형(ratio=1) — 실측 OOS 최선·무편향 (realdata_engine_validation.md)
  - 셀별 추정 = ADR-003 단순 이동평균 (feedback_update.moving_average_update 재사용)
  - 단위 = (site × recipe × ingredient), append-only 원장 + 셀별 running 추정
  - CBR 참고 표시 = 유사 재료구성 과거 레시피의 보정 이력 조회 (decision-support, 자동적용 금지)

⚠ "수렴"은 (레시피×재료) 요구량의 시간적 정상성 가정에 의존 — 종단 파일럿 검증 필요
   (tasks/calibration_pilot_plan.md). 본 PoC는 파이프라인·쿼리 작동을 합성 이벤트로 자기검증.

기준 문서: ADR-008 draft, wiki/design/calibration_direction.md §(4)
담당: Claude PoC (권성민 검토 대기)
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ADR-003 단순 이동평균 재사용 (feedback_update.py 확장)
_ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))
from feedback_update import moving_average_update  # noqa: E402


# ---------------------------------------------------------------------------
# 신뢰도 플래그 (관측 누적 수 기준, lookup.py 신뢰도와 동일 철학)
# ---------------------------------------------------------------------------

def _confidence(n_obs: int) -> str:
    """누적 관측 수 → 신뢰도 플래그.

    Args:
        n_obs: 해당 셀의 누적 보정 관측 수.

    Returns:
        'cold_start'(0) | 'low'(1~2) | 'high'(≥3).
    """
    if n_obs <= 0:
        return "cold_start"
    if n_obs < 3:
        return "low"
    return "high"


@dataclass
class Prediction:
    """추정 결과 (감사용 메타 포함).

    Attributes:
        scaled_g: N인분 예측량 (g).
        method: 'calibrated' | 'cold_start_linear'.
        ratio: 적용된 ratio (= scaled_g/(base×N)).
        n_obs: 캘리브레이션 누적 관측 수.
        confidence: 신뢰도 플래그.
    """
    scaled_g: float
    method: str
    ratio: float
    n_obs: int
    confidence: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cooking_site (
    site_id   INTEGER PRIMARY KEY,
    site_name TEXT,
    site_type TEXT
);
CREATE TABLE IF NOT EXISTS calibration_observation (
    obs_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id           INTEGER NOT NULL,
    recipe_id         INTEGER NOT NULL,
    ingredient_id     INTEGER NOT NULL,
    n_target          INTEGER NOT NULL,
    base_amount_g     REAL    NOT NULL,
    engine_suggested_g REAL   NOT NULL,
    corrected_g       REAL    NOT NULL,
    round_no          INTEGER NOT NULL,
    cbr_shown         INTEGER DEFAULT 0,
    observed_at       TEXT
);
CREATE TABLE IF NOT EXISTS calibration_estimate (
    site_id       INTEGER NOT NULL,
    recipe_id     INTEGER NOT NULL,
    ingredient_id INTEGER NOT NULL,
    est_ratio     REAL    NOT NULL,
    n_obs         INTEGER NOT NULL,
    confidence    TEXT    NOT NULL,
    updated_at    TEXT,
    PRIMARY KEY (site_id, recipe_id, ingredient_id)
);
CREATE INDEX IF NOT EXISTS idx_obs_cell
    ON calibration_observation (site_id, recipe_id, ingredient_id, round_no);
"""


class CalibrationStore:
    """SQLite 기반 캘리브레이션 저장소 (ADR-008 §2.2)."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """저장소 연결 및 스키마 초기화.

        Args:
            db_path: SQLite 경로. 기본 ':memory:'(PoC/테스트용).
        """
        self.con = sqlite3.connect(str(db_path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(_SCHEMA)
        self.con.commit()

    # ----- 쓰기 -----
    def add_site(self, site_id: int, name: str = "", site_type: str = "") -> None:
        """업장(조리현장) 등록. user_group(급식 대상)과 별개 개념."""
        self.con.execute(
            "INSERT OR REPLACE INTO cooking_site VALUES (?,?,?)",
            (site_id, name, site_type),
        )
        self.con.commit()

    def record_observation(
        self,
        site_id: int,
        recipe_id: int,
        ingredient_id: int,
        n_target: int,
        base_amount_g: float,
        corrected_g: float,
        cbr_shown: bool = False,
        observed_at: Optional[str] = None,
    ) -> Prediction:
        """영양사 보정 1건을 원장에 적재하고 셀 추정을 ADR-003 SMA로 갱신.

        Args:
            site_id, recipe_id, ingredient_id: 셀 식별.
            n_target: 목표 인원수 N.
            base_amount_g: 1인분 투입량 (g).
            corrected_g: 영양사 실제 보정량 (g) — 관측된 진실.
            cbr_shown: CBR 참고 표시 노출 여부 (파일럿 A/B용).
            observed_at: ISO 시각 문자열. None이면 현재 UTC.

        Returns:
            갱신 후 셀 추정을 반영한 Prediction.

        Raises:
            ValueError: base_amount_g 또는 n_target ≤ 0.
        """
        if base_amount_g <= 0 or n_target <= 0:
            raise ValueError("base_amount_g>0, n_target>0 필요")
        if observed_at is None:
            observed_at = datetime.now(timezone.utc).isoformat()

        obs_ratio = corrected_g / (base_amount_g * n_target)
        cur = self._get_estimate_row(site_id, recipe_id, ingredient_id)
        n_old = cur["n_obs"] if cur else 0
        ratio_old = cur["est_ratio"] if cur else 1.0  # cold-start=선형

        new_ratio = moving_average_update(ratio_old, n_old, obs_ratio)
        n_new = n_old + 1
        round_no = n_new

        self.con.execute(
            "INSERT INTO calibration_observation "
            "(site_id,recipe_id,ingredient_id,n_target,base_amount_g,"
            "engine_suggested_g,corrected_g,round_no,cbr_shown,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (site_id, recipe_id, ingredient_id, n_target, base_amount_g,
             base_amount_g * n_target, corrected_g, round_no,
             int(cbr_shown), observed_at),
        )
        self.con.execute(
            "INSERT OR REPLACE INTO calibration_estimate "
            "(site_id,recipe_id,ingredient_id,est_ratio,n_obs,confidence,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (site_id, recipe_id, ingredient_id, new_ratio, n_new,
             _confidence(n_new), observed_at),
        )
        self.con.commit()
        return Prediction(
            scaled_g=new_ratio * base_amount_g * n_target,
            method="calibrated",
            ratio=new_ratio,
            n_obs=n_new,
            confidence=_confidence(n_new),
        )

    # ----- 읽기 -----
    def predict(
        self,
        site_id: int,
        recipe_id: int,
        ingredient_id: int,
        base_amount_g: float,
        n_target: int,
    ) -> Prediction:
        """캘리브레이션 추정이 있으면 사용, 없으면 cold-start 선형.

        Args:
            site_id, recipe_id, ingredient_id: 셀 식별.
            base_amount_g: 1인분 투입량 (g).
            n_target: 목표 인원수 N.

        Returns:
            Prediction. 미관측 셀은 method='cold_start_linear', ratio=1.0.
        """
        row = self._get_estimate_row(site_id, recipe_id, ingredient_id)
        if row is None:
            return Prediction(
                scaled_g=base_amount_g * n_target,
                method="cold_start_linear",
                ratio=1.0,
                n_obs=0,
                confidence="cold_start",
            )
        return Prediction(
            scaled_g=row["est_ratio"] * base_amount_g * n_target,
            method="calibrated",
            ratio=row["est_ratio"],
            n_obs=row["n_obs"],
            confidence=row["confidence"],
        )

    def convergence_curve(
        self, site_id: int, recipe_id: int, ingredient_id: int
    ) -> list[dict]:
        """셀의 회차별 수렴 곡선 (P4). 갱신 전 추정으로 다음 관측을 예측한 오차.

        각 회차 t에서 (t−1까지의 추정) 대비 (t 실측)의 절대 백분율 오차를 산출.
        round_no=1은 cold-start(ratio=1) 대비 오차 → 첫 보정의 headroom.

        Args:
            site_id, recipe_id, ingredient_id: 셀 식별.

        Returns:
            [{round_no, obs_ratio, prior_ratio, ape_pct}] 리스트 (회차 오름차순).
        """
        rows = self.con.execute(
            "SELECT n_target, base_amount_g, corrected_g, round_no "
            "FROM calibration_observation "
            "WHERE site_id=? AND recipe_id=? AND ingredient_id=? ORDER BY round_no",
            (site_id, recipe_id, ingredient_id),
        ).fetchall()

        out: list[dict] = []
        prior_ratio = 1.0  # cold-start=선형
        n_seen = 0
        run_ratio = 1.0
        for r in rows:
            obs_ratio = r["corrected_g"] / (r["base_amount_g"] * r["n_target"])
            ape = abs(prior_ratio - obs_ratio) / obs_ratio * 100 if obs_ratio else float("nan")
            out.append({
                "round_no": r["round_no"],
                "obs_ratio": obs_ratio,
                "prior_ratio": prior_ratio,
                "ape_pct": ape,
            })
            run_ratio = moving_average_update(run_ratio, n_seen, obs_ratio)
            n_seen += 1
            prior_ratio = run_ratio
        return out

    def cbr_references(
        self,
        new_ingredient_ids: set[int],
        target_ingredient_id: int,
        top_k: int = 3,
        exclude_recipe_id: Optional[int] = None,
    ) -> list[dict]:
        """CBR retrieve+propose: 재료구성이 유사한 과거 레시피의 target 재료 보정 이력.

        자동 적용 금지 — 표시용(decision-support). 출처(site/recipe/시각) 포함.
        유사도 = 재료 id 집합 Jaccard.

        Args:
            new_ingredient_ids: 신규 레시피의 재료 id 집합.
            target_ingredient_id: 참고를 보고 싶은 대상 재료 id.
            top_k: 반환 개수.
            exclude_recipe_id: 동일 레시피 제외용(자기 자신).

        Returns:
            [{site_id, recipe_id, jaccard, est_ratio, n_obs, updated_at}] (유사도 내림차순).
        """
        # target 재료를 보유한 (site,recipe) 후보의 재료집합 수집
        rows = self.con.execute(
            "SELECT DISTINCT site_id, recipe_id FROM calibration_observation "
            "WHERE ingredient_id=?",
            (target_ingredient_id,),
        ).fetchall()
        refs: list[dict] = []
        for r in rows:
            if exclude_recipe_id is not None and r["recipe_id"] == exclude_recipe_id:
                continue
            ids = {x["ingredient_id"] for x in self.con.execute(
                "SELECT DISTINCT ingredient_id FROM calibration_observation "
                "WHERE site_id=? AND recipe_id=?",
                (r["site_id"], r["recipe_id"]))}
            union = new_ingredient_ids | ids
            jac = len(new_ingredient_ids & ids) / len(union) if union else 0.0
            est = self._get_estimate_row(r["site_id"], r["recipe_id"], target_ingredient_id)
            if est is None:
                continue
            refs.append({
                "site_id": r["site_id"],
                "recipe_id": r["recipe_id"],
                "jaccard": round(jac, 3),
                "est_ratio": round(est["est_ratio"], 3),
                "n_obs": est["n_obs"],
                "updated_at": est["updated_at"],
            })
        refs.sort(key=lambda d: d["jaccard"], reverse=True)
        return refs[:top_k]

    # ----- 내부 -----
    def _get_estimate_row(
        self, site_id: int, recipe_id: int, ingredient_id: int
    ) -> Optional[sqlite3.Row]:
        """셀 추정 1행 조회 (없으면 None)."""
        return self.con.execute(
            "SELECT est_ratio, n_obs, confidence, updated_at "
            "FROM calibration_estimate "
            "WHERE site_id=? AND recipe_id=? AND ingredient_id=?",
            (site_id, recipe_id, ingredient_id),
        ).fetchone()

    def close(self) -> None:
        """연결 종료."""
        self.con.close()
