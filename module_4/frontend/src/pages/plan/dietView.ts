// src/pages/plan/dietView.ts
// 기저질환 그룹 대체식 표시 — 검토 화면(Step2Review)과 PDF·CSV(planExport)가 같은 문구를 쓰도록 모은다.
//  · 하루 기준 결과(MealDay.diet)는 생성 당시 서버가 계산한 값이다. 검토에서 그날 메뉴를 바꾸면 다시 계산하지 않고
//    '수정됨 · 기준 재확인 필요'로 표시한다(daySig 비교).
import { daySig, dietKindOf, type AltTrack, type DietLimit, type MealDay, type MealItem, type DietKind } from '../../api/menu';

/** 기준 이름(짧게) — 표 머리처럼 좁은 곳에 쓴다. 서버 label('탄수화물 열량 비율')은 툴팁·출처에. */
const SHORT_LABEL: Record<string, string> = { sodium: '나트륨', carb_ratio: '탄수화물', sugar: '당류' };
const INFO_UNIT: Record<string, string> = { sugar: 'g' };

export const shortLabel = (key: string, label?: string) => SHORT_LABEL[key] ?? label ?? key;

/** 숫자 + 단위. %는 소수 1자리, 나머지는 정수 반올림. null 은 '?'. */
export function fmtValue(v: number | null | undefined, unit: string): string {
  if (v == null) return '?';
  return unit === '%' ? `${(Math.round(v * 10) / 10).toLocaleString()}%` : `${Math.round(v).toLocaleString()}${unit}`;
}

/** 그룹 종류(예전 저장본은 kind 가 없어 계산). */
export const trackKind = (t: AltTrack): DietKind | null => t.kind ?? dietKindOf(t);

/** 기저질환 기준이 붙은 트랙인지(알레르기만 있는 그룹은 false). */
export const hasDiet = (t: AltTrack | undefined) => !!(t && (t.limits?.length || t.pendingDiseases?.length));

/** 기준 한 줄 — '나트륨 2,000mg 이하'. */
export const limitText = (l: DietLimit) => `${shortLabel(l.key, l.label)} ${fmtValue(l.cap, l.unit)} 이하`;

/** 검토에서 그날 메뉴를 바꿔 서버 판정이 더는 맞지 않는 날. */
export const isDietStale = (d: MealDay) => !!d.diet && daySig(d) !== d.diet.sig;

/** 질환 기준 때문에 바꾼 메뉴 표시 — 검토에서 다른 메뉴로 바꾼 뒤에는 걷는다(orig 가 생김). */
export const dietSwapOf = (it: MealItem) => (it.dietSwap && !it.orig ? it.dietSwap : undefined);

/** 하루 기준별 합계 줄 — '나트륨 1,850/2,000mg'. */
export function dietDayLines(d: MealDay): { key: string; text: string; ok: boolean; label: string }[] {
  return Object.entries(d.diet?.limits ?? {}).map(([key, r]) => ({
    key, ok: r.ok, label: r.label,
    text: `${shortLabel(key, r.label)} ${fmtValue(r.total, r.unit).replace(/[^\d.,?]+$/, '')}/${fmtValue(r.cap, r.unit)}`,
  }));
}

/** 참고값(기준 없음) — '당류 32g(참고)'. 값을 모르는 메뉴가 있으면 '· N개 메뉴 정보 없음'. */
export function dietInfoLines(d: MealDay): string[] {
  return Object.entries(d.diet?.info ?? {}).map(([key, v]) =>
    `${shortLabel(key)} ${fmtValue(v.total, INFO_UNIT[key] ?? '')}(참고)${v.unknown ? ` · ${v.unknown}개 메뉴 정보 없음` : ''}`);
}

/** 트랙 전체 — 기준 충족 일수. 메뉴를 고친 날은 충족으로 세지 않는다(재확인 필요). */
export function dietSummary(t: AltTrack): { met: number; total: number; stale: number } {
  const days = t.weeks.flatMap((w) => w.days).filter((d) => d.diet);
  const stale = days.filter(isDietStale).length;
  const met = days.filter((d) => d.diet!.status === '충족' && !isDietStale(d)).length;
  return { met, total: days.length, stale };
}

/** 이날 상태 문구(PDF·CSV 비고). 문제가 없으면 ''. */
export function dietDayNote(d: MealDay): string {
  if (!d.diet) return '';
  if (isDietStale(d)) return '메뉴 수정됨 · 질환 기준 재확인 필요';
  if (d.diet.status === '충족') return '';
  const bad = dietDayLines(d).filter((l) => !l.ok).map((l) => l.text);
  const unknown = d.diet.unknownMenus.length ? [`영양 정보 없음: ${d.diet.unknownMenus.join(', ')}`] : [];
  return `질환 기준 확인 필요(${[...bad, ...unknown].join(' · ') || '판정 불가'})`;
}

/** 기준 미반영 질환 안내(신장질환 등). */
export const pendingText = (t: AltTrack) =>
  t.pendingDiseases?.length ? `${t.pendingDiseases.join('·')}은 기준을 정하는 중이라 반영되지 않았습니다` : '';
