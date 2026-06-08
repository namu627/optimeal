---
type: decision
tags: [adr, 도구, llm, 로컬]
source: docs/decisions_summary_v5_7.md
updated: 2026-04-06
---

# ADR-005 — 로컬 LLM 코딩 보조 도입

## 결정 (2026-02-26)

**Qwen2.5-Coder:14B (Ollama 로컬 운용) 채택. 생성 코드는 Claude API 2단계 검토 의무화.**

## 하드웨어

- PC 1 (Windows): RTX 3060 12GB VRAM, RAM 64GB → Q4_K_M 완전 로드 가능
- 실기 평가: 단일 함수 84점 / 클래스·API 단위 64점 / 종합 75.8점

## 활용 범위 (모듈별)

| 모듈 | 활용도 | 주요 작업 |
|------|--------|----------|
| 모듈2 정규화 | ★★★★★ | unit_conversion, ingredient_synonym 파서 |
| 모듈2 매칭 | ★★★★ | 다층 유사도 함수, INSERT 스크립트 |
| 모듈2 통계 | ★★★ | fit_power_law, MixedLM 결과 파싱 |
| 모듈2 ML | ★★★ | IngredientClassifier 메서드 |
| 모듈3+백엔드 | ★★★ | FastAPI 엔드포인트 초안 |
| 모듈4 프론트 | ★★★★ | React 컴포넌트 |

## 사용 금지 범위

- 시스템 아키텍처 설계
- MixedLM 통계 전략 검토
- **클래스 전체 생성** ← 함수/메서드 단위만 허용
- 방법론 학술 검토

## 2단계 프로세스 (의무)

```
로컬 LLM 생성 → Claude API 검토 → 단위 테스트
```

import 경로 및 학습-추론 파이프라인 일관성 필수 확인
