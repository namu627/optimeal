"""
config.py
=========
모듈 4 백엔드 설정 (환경변수 기반).

경로·선택적 의존성(모듈 3 CSP, PostgreSQL)을 한곳에서 해석한다.
선택적 의존성이 없어도 앱은 정상 기동해야 하며, 해당 엔드포인트만 503을 반환한다
(ADR-008 이후 운영 핵심은 캘리브레이션이고, CSP·영양성분 DB는 별도 인프라에 있음).

환경변수:
    OPTIMEAL_CALIBRATION_DB : 캘리브레이션 SQLite 경로 (기본 data/processed/calibration.db)
    OPTIMEAL_MODULE3_PATH   : 모듈 3 `src` 디렉터리 (팀 repo namu627/optimeal)
    POSTGRES_*              : 영양성분 DB 접속 정보 (모듈 1)
"""

from __future__ import annotations

import os
from pathlib import Path

# 01_Claude-code 루트 (module_4/backend/src/config.py → 3단계 상위)
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# 데이터 산출물 경로 (읽기 전용 자산)
# ---------------------------------------------------------------------------
DF_B_CSV = PROJECT_ROOT / "module_2" / "df_B.csv"
COEFFICIENTS_CSV = (
    PROJECT_ROOT / "module_2" / "src" / "engine" / "scaling_coefficients.csv"
)

# 캘리브레이션 원장 (쓰기 대상 — 유일하게 API가 변경하는 상태)
CALIBRATION_DB = Path(
    os.getenv("OPTIMEAL_CALIBRATION_DB", PROJECT_ROOT / "data" / "processed" / "calibration.db")
)


def module3_src_path() -> Path | None:
    """모듈 3(CSP) `src` 디렉터리를 찾는다.

    탐색 순서: 환경변수 → **같은 저장소의 module_3**(팀 repo develop 기준 정본)
    → 형제 팀 repo(`../02_GitHub/optimeal`, 01_Claude-code 단독 실행 시 폴백).

    Returns:
        존재하는 첫 경로. 없으면 None (→ /api/menu/generate 가 503).
    """
    candidates = []
    env = os.getenv("OPTIMEAL_MODULE3_PATH")
    if env:
        candidates.append(Path(env))
    candidates.append(PROJECT_ROOT / "module_3" / "src")
    candidates.append(PROJECT_ROOT.parent / "02_GitHub" / "optimeal" / "module_3" / "src")
    for c in candidates:
        if c.is_dir() and (c / "csp_solver.py").exists():
            return c
    return None


def postgres_url() -> str:
    """영양성분 DB(PostgreSQL) 접속 URL. 팀 docker-compose 규약과 동일한 환경변수 사용."""
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "optimeal")
    user = os.getenv("POSTGRES_USER", "optimeal")
    password = os.getenv("POSTGRES_PASSWORD", "optimeal_dev_pw")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


# ---------------------------------------------------------------------------
# ADR-008 정직성 고지 — 스케일링 응답에 항상 동봉한다.
# ---------------------------------------------------------------------------
COLD_START_DISCLAIMER = (
    "초기 추정은 선형(b=1)이며 '예측'이 아니라 '출발점'입니다. "
    "실측 검증(6루프)에서 현 데이터로는 선형이 사실상 최선이었고 비선형 모델은 "
    "OOS에서 이를 이기지 못했습니다(ADR-008). 정확도는 영양사 보정 누적으로 개선됩니다."
)
CBR_DISCLAIMER = (
    "CBR 참고 이력은 과거 다른 업장·레시피의 영양사 실보정값입니다. "
    "자동 적용되지 않으며(auto_apply=false) 판단 보조용으로만 표시합니다."
)
