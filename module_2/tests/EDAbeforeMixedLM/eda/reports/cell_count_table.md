# 셀별 관측 수 집계 — ADR-002 v2 §4.2

**생성일:** 2026-03-20
**기준:** 셀당 n ≥ 10 → SEPARATE, n < 10 → MERGE (3대분류 유지)
⚠️ 기계적 적용 — 주관적 조정 금지 (TASK 명세 §5.2 준수)

---

## 조리방법 세분류 × role 교차표 (n)

| cooking_method   |   main |   seasoning |   sub |   TOTAL |
|:-----------------|-------:|------------:|------:|--------:|
| 굽기               |     15 |          12 |    34 |      61 |
| 끓이기              |     32 |          45 |    78 |     155 |
| 끓이기(삶기)          |     28 |          71 |   108 |     207 |
| 무치기              |     12 |         129 |    44 |     185 |
| 볶기               |     35 |         115 |    78 |     228 |
| 절이기(담그기)         |      0 |          10 |     8 |      18 |
| 조리기(졸이기)         |      1 |           5 |     0 |       6 |
| 졸이기              |      3 |           5 |     7 |      15 |
| 졸이기(조리기)         |      8 |           7 |     5 |      20 |
| 찌기               |      5 |          10 |     4 |      19 |
| 튀기기              |      6 |           6 |    24 |      36 |

---

## SEPARATE / MERGE 결정

| cooking_method | role | n | 결정 |
|----------------|------|---|------|
| 굽기 | main | 15 | SEPARATE |
| 굽기 | seasoning | 12 | SEPARATE |
| 굽기 | sub | 34 | SEPARATE |
| 끓이기 | main | 32 | SEPARATE |
| 끓이기 | seasoning | 45 | SEPARATE |
| 끓이기 | sub | 78 | SEPARATE |
| 끓이기(삶기) | main | 28 | SEPARATE |
| 끓이기(삶기) | seasoning | 71 | SEPARATE |
| 끓이기(삶기) | sub | 108 | SEPARATE |
| 무치기 | main | 12 | SEPARATE |
| 무치기 | seasoning | 129 | SEPARATE |
| 무치기 | sub | 44 | SEPARATE |
| 볶기 | main | 35 | SEPARATE |
| 볶기 | seasoning | 115 | SEPARATE |
| 볶기 | sub | 78 | SEPARATE |
| 절이기(담그기) | seasoning | 10 | SEPARATE |
| 절이기(담그기) | sub | 8 | MERGE |
| 조리기(졸이기) | main | 1 | MERGE |
| 조리기(졸이기) | seasoning | 5 | MERGE |
| 졸이기 | main | 3 | MERGE |
| 졸이기 | seasoning | 5 | MERGE |
| 졸이기 | sub | 7 | MERGE |
| 졸이기(조리기) | main | 8 | MERGE |
| 졸이기(조리기) | seasoning | 7 | MERGE |
| 졸이기(조리기) | sub | 5 | MERGE |
| 찌기 | main | 5 | MERGE |
| 찌기 | seasoning | 10 | SEPARATE |
| 찌기 | sub | 4 | MERGE |
| 튀기기 | main | 6 | MERGE |
| 튀기기 | seasoning | 6 | MERGE |
| 튀기기 | sub | 24 | SEPARATE |

---

## ADR-002 v2 §4.2 결론

- SEPARATE 셀 수: **18개** (n ≥ 10)
- MERGE 대상 셀 수: **13개** (n < 10)
- **결론:** n < 10인 조리방법 세분류는 3대분류(건열/습열/비가열)로 통합
