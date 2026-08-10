"""
conftest.py
===========
모듈 4 백엔드 테스트 픽스처.

캘리브레이션 원장은 상태를 가지므로 테스트마다 **임시 SQLite 파일**로 격리한다
(`:memory:`는 요청당 커넥션 구조에서 상태가 유지되지 않아 쓸 수 없다).
`importlib.reload(config)`는 모듈 객체를 그 자리에서 갱신하므로, 이미 config 를
import 해 둔 라우터들도 새 경로를 보게 된다.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """격리된 캘리브레이션 DB를 가진 TestClient 를 만든다."""
    monkeypatch.setenv("OPTIMEAL_CALIBRATION_DB", str(tmp_path / "calib.db"))
    from module_4.backend.src import config

    importlib.reload(config)
    from module_4.backend.src.main import create_app

    with TestClient(create_app()) as c:
        yield c
