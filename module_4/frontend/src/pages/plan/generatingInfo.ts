// src/pages/plan/generatingInfo.ts
// 생성 중 화면(GeneratingOverlay)에 넘기는 조건과 예상 소요 시간.

/** 실제로 보낸 요청 기준 — 기간·끼니(아침/점심/저녁)·인원·알레르기 그룹 유무. */
export interface GeneratingInfo { days: number; meals: string[]; headcount: number; hasAllergy: boolean }

/** 예상 소요(초) — 화면 기본 조건 측정값 기준(2026-10-01~02). 1일 ~8초, 7일 점심 ~22초, 7일 3식 38~41초,
 *  31일 점심 ~50초, 31일 2식 54~63초. */
export function estimateSeconds(days: number, mealCount: number): number {
  if (days <= 1) return 10;
  if (days <= 7) return mealCount >= 2 ? 40 : 25;
  return mealCount >= 2 ? 60 : 50;
}
