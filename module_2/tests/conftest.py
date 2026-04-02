"""
conftest.py
===========
pytest 경로 설정 — module_2/tests/ 하위 테스트에서
src/engine 모듈(lookup, fallback)을 직접 임포트할 수 있도록 sys.path에 추가한다.
"""

import sys
from pathlib import Path

# module_2/src/engine 을 import 검색 경로에 추가
_ENGINE_DIR = Path(__file__).parent.parent / "src" / "engine"
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))
