# _vendor — swagger-ui-dist (오프라인 self-contained용)

`scripts/gen_openapi.py`가 self-contained Swagger HTML을 만들 때 인라인하는 정적 자산.
출처: swagger-ui-dist@5.17.14 (npm). CDN 의존 없이 오프라인에서 문서가 렌더되도록 vendoring.
API 변경 시: `python scripts/gen_openapi.py` 재실행 → openapi.json + HTML 동시 갱신.