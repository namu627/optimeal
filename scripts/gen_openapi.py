#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_openapi.py
==============
Swagger 문서 생성기 — **FastAPI 앱이 유일한 원천(single source of truth)**.

앱(main.create_app)의 OpenAPI 스펙을 뽑아 두 산출물을 만든다:
  1) docs/api/openapi.json                — OpenAPI 3.1 스펙 (손수정 금지, 이 스크립트로만 생성)
  2) docs/api/OptiMeal_API_Swagger.html   — self-contained Swagger UI (스펙 + swagger-ui 번들 인라인)

이렇게 하면 요청 예시·연동 안내·servers 가 전부 코드(schemas.py / main.py)에서 오고,
사본이 어긋날 여지가 없다. API 변경 시 이 스크립트만 재실행하면 두 산출물이 동기화된다.

실행:  python scripts/gen_openapi.py
swagger-ui 번들은 docs/api/_vendor/ 에 vendored (오프라인·self-contained 보장).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from module_4.backend.src.main import create_app  # noqa: E402

OUT = ROOT / "docs" / "api"
VENDOR = OUT / "_vendor"


def _guard(s: str) -> str:
    """<script> 인라인 안전화: 종료 태그 시퀀스 차단."""
    return s.replace("</script>", "<\\/script>").replace("</SCRIPT>", "<\\/SCRIPT>")


def main() -> int:
    spec = create_app().openapi()
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) openapi.json (단일 원천)
    (OUT / "openapi.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2) self-contained Swagger UI HTML
    css = (VENDOR / "swagger-ui.css").read_text(encoding="utf-8")
    js = (VENDOR / "swagger-ui-bundle.js").read_text(encoding="utf-8")
    spec_js = _guard(json.dumps(spec, ensure_ascii=False))

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>OptiMeal API — Swagger 문서 (프론트엔드 개발자용)</title>
<style>{css}</style>
<style>
  body{{margin:0;background:#fafafa}}
  .topbar{{background:#1b1b2f;color:#fff;padding:14px 24px;
           font:600 16px/1.4 system-ui,'Malgun Gothic',sans-serif}}
  .topbar small{{display:block;font-weight:400;color:#b7b7d0;margin-top:4px;font-size:12px}}
  #swagger-ui{{max-width:1200px;margin:0 auto}}
</style>
</head>
<body>
<div class="topbar">OptiMeal API — Swagger 문서
  <small>모듈 4 백엔드 REST API (FR-12) · 프론트엔드 연동 기준 · scripts/gen_openapi.py 로 생성</small>
</div>
<div id="swagger-ui"></div>
<script>{_guard(js)}</script>
<script>
const spec = {spec_js};
window.ui = SwaggerUIBundle({{
  spec: spec, dom_id: '#swagger-ui', deepLinking: true,
  docExpansion: 'list', defaultModelsExpandDepth: 1, tryItOutEnabled: true,
  presets: [SwaggerUIBundle.presets.apis],
  layout: 'BaseLayout'
}});
</script>
</body>
</html>"""
    (OUT / "OptiMeal_API_Swagger.html").write_text(html, encoding="utf-8")

    n_ep = sum(len(v) for v in spec["paths"].values())
    print(f"[생성 완료] docs/api/openapi.json · OptiMeal_API_Swagger.html "
          f"(엔드포인트 {n_ep} · 스키마 {len(spec.get('components', {}).get('schemas', {}))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())