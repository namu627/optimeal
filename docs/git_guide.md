# OptiMeal Git 협업 가이드

**대상**: 팀원 전체 | **최초 작성**: 2026-04

---

## 1. 브랜치 전략 개요

우리 팀은 **역할 고정 없이 주차별 태스크를 유동 분담**하는 방식으로 일한다.
따라서 브랜치도 "사람" 또는 "영구 기능 영역"이 아닌 **태스크 단위**로 만든다.

```
main
 ├── task/Back-lookup-fallback        ← 남유찬 (이번 주)
 ├── task/Back-inverse-transform      ← 박소희, 박미연 (이번 주)
 ├── task/Common-paper-limitations       ← 권성민 (이번 주)
 └── task/Common-next-sprint-plan        ← 권성민 (이번 주)
```

### 브랜치 2종류만 사용

| 브랜치 | 규칙 | 누가 만드나 |
|--------|------|-------------|
| `main` | 검토 완료된 작업만 병합. 항상 동작하는 상태 유지. | — |
| `task/[태스크ID]-[설명]` | 하나의 주차 태스크 = 하나의 브랜치. 병합 후 삭제. | 해당 태스크 담당자 |

> **왜 단순하게 유지하는가?**
> 소규모 팀에서 브랜치 구조가 복잡해지면 관리 비용이 개발 비용을 초과한다.
> `main` + `task/` 2단계로 충분하다.

---

## 2. 브랜치 이름 규칙

```
task/[태스크ID]-[내용 2~4단어]
태스크ID는 간트차트의 태스크별 '파트이름'을 그대로 사용한다.

예시:
  task/Data-lookup-fallback
  task/Data-inverse-transform
  task/Common-paper-limitations
  task/Common-next-sprint-plan
  task/AI-ingredient-classifier
  task/Back-mixedlm-rerun
```

- 영어 소문자와 하이픈(`-`)만 사용. 공백·한국어 금지.

---

## 3. 작업 흐름 (매 태스크마다 반복)

### 3-1. 태스크 시작할 때

```bash
# 1. 최신 main을 받아온다
git checkout main
git pull origin main

# 2. 내 태스크 브랜치를 만들고 이동
git checkout -b task/D1-lookup-fallback
```

### 3-2. 작업 중 (매일 끝날 때)

```bash
# 변경한 파일만 스테이징 (git add . 가급적 피하기)
git add module_2/src/engine/lookup.py
git add module_2/tests/test_lookup.py

# 커밋 메시지: [태스크ID] 한 줄 요약
git commit -m "D1: v_scaling_lookup 조회 함수 기본 구조 구현"

# 원격에 push (처음에는 -u 옵션)
git push -u origin task/D1-lookup-fallback
# 이후부터는
git push
```

**커밋 메시지 형식:**
```
[태스크ID]: 한 줄 요약 (50자 이내)

# 예시:
Back: v_scaling_lookup 조회 함수 구현
Back: Fallback 체인 추가 (category_mean → 전체평균)
Back: Y = a × base × N^b 역변환 함수 구현
Back: 단위 테스트 3케이스 추가 (seasoning, main, sub)
Common: H0₁ 한계 절 초안 작성
```

### 3-3. main에 최신 작업 반영받기 (충돌 예방)

**다른 팀원의 작업이 main에 병합되었을 때**, 내 브랜치에 반영한다.

```bash
# 현재 내 태스크 브랜치에서
git fetch origin
git merge origin/main

# 충돌(conflict)이 생기면 → 4절 참고
```

> **언제 해야 하나?**
> - 다른 팀원이 내가 수정 중인 파일 근처를 건드렸다는 걸 알았을 때
> - 태스크가 3일 이상 길어질 때 (하루에 한 번 권장)

### 3-4. 태스크 완료 → main에 병합

```bash
# 1. 최종 push
git push

# 2. GitHub에서 Pull Request 생성
#    - 제목: "[태스크ID] 태스크 이름"  예) "[D1] v_scaling_lookup 조회 함수 + Fallback 체인"
#    - 리뷰어: 협업 팀원.

# 3. 리뷰어가 승인하면 GitHub에서 "Merge pull request" 클릭
#    (또는 리뷰어가 직접 merge)

# 4. 병합 후 브랜치 삭제 (GitHub UI에서 "Delete branch" 클릭)
#    로컬에서도 정리:
git checkout main
git pull origin main
git branch -d task/D1-lookup-fallback
```

---

## 4. 충돌(Conflict) 해결

### 충돌이 생겼을 때 터미널 메시지

```
Auto-merging module_2/src/engine/lookup.py
CONFLICT (content): Merge conflict in module_2/src/engine/lookup.py
Automatic merge failed; fix conflicts and then commit the result.
```

### 해결 순서

```bash
# 1. 충돌 파일 확인
git status
# "both modified" 표시된 파일들이 충돌 파일

# 2. 해당 파일을 열면 이런 표시가 있음:
#   <<<<<<< HEAD (내 변경)
#   내가 작성한 코드
#   =======
#   상대방이 작성한 코드
#   >>>>>>> origin/main (main에서 온 변경)

# 3. 둘 중 하나를 선택하거나, 두 코드를 합쳐서 편집
#    (표시 기호 <<<, ===, >>> 는 모두 지워야 함)

# 4. 해결 완료 후
git add [충돌해결한파일]
git commit -m "D1: merge origin/main, lookup.py 충돌 해결"
```

### 충돌 예방 방법

| 상황 | 대처 |
|------|------|
| 같은 파일을 두 명이 동시에 수정할 것 같을 때 | 태스크 분할 시 파일 단위로 담당 나누기 |
| 테스트 파일 (`conftest.py`) | 공용 파일이므로 수정 전 팀 채팅으로 공지 |
| 데이터 파일 (`.xlsx`, `.csv`) | 한 명만 수정하는 것을 원칙으로 함 |

---

## 5. 이번 주 브랜치 예시 (3/30~4/6 스프린트)

```bash
# 남유찬
git checkout -b task/Back-lookup-fallback

# 박소희, 박미연
git checkout -b task/Back-inverse-transform

# 권성민
git checkout -b task/Common-paper-limitations

# 권성민
git checkout -b task/Common-next-sprint-plan
```

### 의존성이 있는 태스크 처리 (D1 + D2 → D 통합테스트)

```
D1 브랜치 (남유찬)       ──┐
                        ├── main에 각각 병합 → 남유찬, 박미연, 박소희 셋 다 main pull 후 통합테스트 진행
D2 브랜치 (박소희, 박미연) ──┘
```

D1, D2가 모두 main에 병합된 뒤:

```bash
# W, X 둘 다 실행
git checkout main
git pull origin main

# 통합 테스트용 새 브랜치 만들거나 (또는 main에서 직접)
git checkout -b task/D-integration-test
pytest module_2/tests/ -v
# 결과 확인 후 문서 업데이트하고 main에 병합
```

---

## 6. 자주 쓰는 명령어 모음

```bash
# 현재 브랜치 확인
git branch

# 원격 브랜치 포함 전체 확인
git branch -a

# 특정 브랜치로 이동
git checkout task/D1-lookup-fallback

# 브랜치 목록 + 최신 커밋 메시지 확인
git branch -v

# 커밋 히스토리 한 줄로 보기
git log --oneline --graph --all

# 아직 커밋 안 한 변경사항 임시 저장 (급하게 브랜치 바꿔야 할 때)
git stash
git stash pop  # 다시 꺼내기

# 실수로 git add한 파일 되돌리기 (파일은 보존)
git restore --staged [파일명]

# 마지막 커밋 메시지 수정 (push 전에만)
git commit --amend -m "새 메시지"
```

---

## 7. Pull Request 작성 요령

GitHub에서 PR을 만들 때 아래 형식을 사용한다.

**제목**: `[D1] v_scaling_lookup 조회 함수 + Fallback 체인`

**본문**:
```
## 작업 내용
- v_scaling_lookup 뷰 기반 파라미터 조회 함수 구현
- lookup_priority ASC LIMIT 1 방식으로 3순위 조회
- Fallback 체인: group_type → category_mean → 전체평균
- 신뢰도 플래그 반환 (high/medium/low)

## 테스트
- [ ] pytest module_2/tests/test_lookup.py 전체 PASS

## 리뷰 포인트
- fallback.py L42: category_mean 집계 방식 의도 확인 요청
```

**리뷰어**: 태스크 분할표의 검토 담당자 

---

## 8. 최초 참여 시 환경 세팅 (처음 한 번만)

---

```bash
# 저장소를 통째로 가져오기 (git init 불필요)
git clone https://github.com/[팀계정]/OptiMeal.git
cd OptiMeal

# 환경 세팅
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 9. 규칙 요약

| 규칙 | 내용 |
|------|------|
| main 직접 push 금지 | 반드시 task/ 브랜치 → PR → 병합 |
| 브랜치 1개 = 태스크 1개 | 한 브랜치에 여러 태스크 섞지 않기 |
| 병합 후 브랜치 삭제 | 완료된 브랜치는 즉시 정리 |
| 매일 시작 전 pull | `git pull origin main` 습관화 |
| 커밋 단위는 작게 | "완료" 기다리지 말고 의미 있는 단위마다 커밋 |
| data/raw/ 수정 시 공지 | 원본 데이터 변경은 팀 채팅에 먼저 알리기 |
