# OptiMeal Docker 이미지 태그 문서

> 각 이미지 태그가 **어떤 환경(Python 버전 · 주요 라이브러리 · 소스 커밋)** 으로 빌드되었는지 기록한다.
> 발표·제출·재현 시 "정확히 어떤 환경이었나"를 되짚기 위한 문서다.
> 새 이미지를 빌드할 때마다 **표에 행을 추가**한다. (기존 행은 수정하지 않고 누적)

---

## 태그 규칙

- 형식: `optimeal_app:vMAJOR.MINOR` (예: `v1.0`, `v1.1`)
- **`latest` 태그에 의존하지 않는다.** 재현 대상은 항상 버전 태그.
- MAJOR: 베이스 이미지(Python)나 핵심 패키지가 크게 바뀔 때 올린다.
- MINOR: 라이브러리 일부 추가/갱신 등 소규모 변경.

---

## 이미지 이력

| 이미지 태그 | 빌드 날짜 | Python 버전 | 베이스 이미지 | 주요 패키지 버전 | requirements 커밋 | 비고 |
|---|---|---|---|---|---|---|
| `optimeal_app:v1.0` | 2026-08-04 | 3.12.13 | `python:3.12.13-slim` | pandas==2.2.3 / numpy==1.26.4 / scikit-learn==1.5.2 / scipy==1.13.1 / statsmodels==0.14.4 / xgboost==2.1.3 / lightgbm==4.5.0 / psycopg2-binary==2.9.10 | (커밋 대기 — requirements.txt·Dockerfile 커밋 후 해시 기입 필요) | 최초 버전 고정. torch는 컨테이너에 미설치 상태라 목록에서 제외 |
| | | | | | | |

> 표 채우는 값 출처:
> - Python 버전: `docker exec optimeal_app python --version`
> - 베이스 이미지: Dockerfile의 `FROM` 라인
> - 주요 패키지 버전: `requirements.txt`에서 핵심 패키지 발췌
> - requirements 커밋: `requirements.txt`가 커밋된 Git 해시 (`git rev-parse --short HEAD`)

---

## 태그별 상세

### `optimeal_app:v1.0`

- **빌드 명령**: `docker build --no-cache -f docker/Dockerfile -t optimeal_app:v1.0 .`
- **빌드 날짜**: 2026-08-04
- **Python**: 3.12.13
- **베이스 이미지**: `python:3.12.13-slim`
- **DB 컨테이너 연동**: `optimeal_db` (PostgreSQL 18.1)
- **핵심 패키지**:
  - pandas==2.2.3
  - numpy==1.26.4
  - scikit-learn==1.5.2
  - scipy==1.13.1
  - statsmodels==0.14.4
  - xgboost==2.1.3
  - lightgbm==4.5.0
  - psycopg2-binary==2.9.10
  - sqlalchemy==2.0.36
- **전체 의존성**: `requirements.txt` 참조 (커밋 대기 — 커밋 후 해시 기입 필요)
- **검증**: `docker run --rm optimeal_app:v1.0 pip freeze` 결과가 `requirements.txt`와 일치 확인 (diff 없음, Python 3.12.13 동일 확인)
- **비고**: 최초로 전체 버전을 고정한 baseline 이미지. 기존 실행 중이던 `optimeal_app` 컨테이너(이미지명 `optimeal-app`) 스냅샷을 기준으로 재현.

---

<!--
다음 버전 추가 시 아래 블록을 복사해서 사용:

### `optimeal_app:vX.X`

- **빌드 명령**: `docker build --no-cache -t optimeal_app:vX.X .`
- **빌드 날짜**: 2026-XX-XX
- **Python**: 3.x.x
- **베이스 이미지**: `python:3.x.x-slim`
- **핵심 패키지**: pandas==... / scikit-learn==... / torch==... / psycopg2-binary==...
- **전체 의존성**: requirements.txt 참조 (커밋 XXXXXXX)
- **검증**: pip freeze == requirements.txt (diff 없음)
- **비고**: (v(X-1).X 대비 변경점 요약)
-->
