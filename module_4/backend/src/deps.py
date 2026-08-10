"""
deps.py
=======
의존성 주입 — 캘리브레이션 저장소 수명 관리.

`CalibrationStore`는 `sqlite3.connect()`를 그대로 들고 있어 **생성 스레드에서만** 사용
가능하다. FastAPI는 동기 엔드포인트를 스레드풀에서 실행하므로 저장소를 전역 싱글턴으로
두면 `ProgrammingError: SQLite objects created in a thread...`가 난다.
→ **요청당 1 커넥션**을 열고 응답 후 닫는다(SQLite가 쓰기를 직렬화하므로 안전).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

from . import config

# module_2/src/calibration 을 import 경로에 추가 (패키지 설치 없이 재사용)
_CALIB_DIR = config.PROJECT_ROOT / "module_2" / "src" / "calibration"
if str(_CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(_CALIB_DIR))

from calibration_store import CalibrationStore  # noqa: E402


def _db_path() -> Path:
    """캘리브레이션 DB 경로를 보장(디렉터리 생성 포함)."""
    p = Path(config.CALIBRATION_DB)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_store() -> Iterator[CalibrationStore]:
    """요청 단위 CalibrationStore 를 제공하고 종료 시 닫는다 (FastAPI Depends용)."""
    store = CalibrationStore(_db_path())
    try:
        yield store
    finally:
        store.close()
