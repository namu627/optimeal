---
type: status
tags: [미결, 이슈, 블로커]
source: tasks/todo.md
updated: 2026-04-06
---

# 미결 사항 (Open Issues)

## 긴급 (5/4 피드백 전 필수)

### ⚠ se_b 적재 확인

```sql
SELECT COUNT(*) FROM scaling_coefficient WHERE version=1 AND se_b IS NULL;
-- 결과 0건 필수
```

- 목적: 출력 클리핑 임계값 ε = 2×se_b 코드에서 DB 참조 가능하도록
- ADR-003: se_b 코드 하드코딩 금지, 반드시 DB에서 읽을 것
- → [[decisions/adr_003_clipping]]

## 일반

### git tag v-stat-complete 미부여

- 목적: 통계 분석 완료 시점 재현성 확보 (ADR 의무)
- 다음 태그: `v-module2-complete` (모듈 2 완성 시)

### GitHub 원격 저장소 이전 미완

- 기존 커밋 히스토리 포함 이전 필요

## 4월 내 확인 필요

### 영양사 평가자 확보

- 최소 2명 필수 (단일 평가자 논문 방어 위험)
- Cohen's Kappa ≥ 0.6 목표
- **4/19 이전 확정 필수** (PRD v1.4 리스크 항목)

### FR-07 범위 결정

- 4월 MixedLM 완료 후 셀별 SE(b) 확인
- SE(b) > 0.2인 셀이 전체의 50% 이상 → FR-07 축소 또는 제외
- `category_mean` fallback으로 대체 가능

→ 진행 현황: [[status/module2_progress]]
