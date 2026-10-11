// src/pages/plan/dietGroups.ts
// 조건 입력 화면 '제한 조건 그룹'(알레르기·기저질환)의 화면 밖 로직 — 검사·전송값·합계.
// 화면(RestrictionGroupsField·Step1Conditions)과 테스트가 같이 쓴다.
import type { DietGroup } from '../../api/menu';

/** 새 그룹 한 줄. i = 0부터의 위치. */
export const emptyGroup = (i: number): DietGroup => ({ label: `그룹 ${i + 1}`, allergens: [], diseases: [], count: 0 });

/** 고른 조건(알레르기·기저질환)이 하나라도 있는지. */
export const hasCondition = (g: DietGroup) => g.allergens.length > 0 || g.diseases.length > 0;

/** 손대지 않은 줄(조건 없음·0명) — 전송·검사에서 뺀다. */
export const isUntouched = (g: DietGroup) => !hasCondition(g) && !(g.count > 0);

/** 예전 스냅샷·저장본(diseases 없음)도 받는다. */
export function normalizeGroups(list: Array<Partial<DietGroup>> | undefined): DietGroup[] {
  const out = (list ?? []).map((g, i) => ({
    label: `그룹 ${i + 1}`, allergens: [...(g.allergens ?? [])], diseases: [...(g.diseases ?? [])], count: g.count ?? 0,
  }));
  return out.length ? out : [emptyGroup(0)];
}

/** 인원 합(모든 줄). */
export const groupTotal = (groups: DietGroup[]) => groups.reduce((s, g) => s + (g.count || 0), 0);

export interface GroupProblems {
  /** 인원은 넣었는데 조건을 고르지 않은 줄(0부터의 위치) */
  noCondition: number[];
  /** 조건은 골랐는데 인원이 0인 줄 */
  noCount: number[];
  /** 그룹 인원 합 - 전체 인원(넘지 않으면 0) */
  over: number;
}

export function groupProblems(groups: DietGroup[], headcount: number): GroupProblems {
  const noCondition: number[] = [], noCount: number[] = [];
  groups.forEach((g, i) => {
    if (isUntouched(g)) return;
    if (!hasCondition(g)) noCondition.push(i);
    else if (!(g.count > 0)) noCount.push(i);
  });
  return { noCondition, noCount, over: Math.max(0, groupTotal(groups) - headcount) };
}

/** 빨간 테두리를 그릴 줄. */
export const problemRows = (p: GroupProblems) => new Set([...p.noCondition, ...p.noCount]);

const names = (idx: number[]) => idx.map((i) => `그룹 ${i + 1}`).join(', ');

/** 식단 생성을 막는 안내 문구. 문제가 없으면 null. */
export function problemMessage(p: GroupProblems): string | null {
  if (p.noCondition.length) return `${names(p.noCondition)}에 선택한 조건이 없습니다. 조건을 선택하거나 그룹을 삭제해 주세요.`;
  if (p.noCount.length) return `${names(p.noCount)}의 인원을 입력해 주세요.`;
  if (p.over) return `그룹 인원 합이 전체 인원보다 ${p.over.toLocaleString()}명 많습니다. 겹치는 사람은 복합 그룹 하나로 옮겨 주세요.`;
  return null;
}

/** 백엔드 diet_groups — 손대지 않은 줄은 빼고, 이름은 화면 번호 그대로('그룹 3'). */
export function toDietGroups(groups: DietGroup[]): DietGroup[] {
  return groups
    .map((g, i) => ({ label: `그룹 ${i + 1}`, allergens: [...g.allergens], diseases: [...g.diseases], count: g.count }))
    .filter((g) => !isUntouched(g));
}
