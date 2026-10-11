// 기저질환(고혈압·당뇨) 프론트 — 조건 입력 검사·전송값, 저장본 복원, 응답 매핑, 검토 표시·PDF 문구.
import { describe, expect, it } from 'vitest';

import { toMealPlan, type DietGroup, type MealPlan, type MenuGenerateRaw, type MenuGenerateRequest } from '../../api/menu';
import { editPlan } from './altSync';
import { groupsFromInputs } from './cloneForm';
import { groupProblems, problemMessage, problemRows, toDietGroups } from './dietGroups';
import { dietDayLines, dietDayNote, dietSummary, dietSwapOf, isDietStale, pendingText } from './dietView';
import { altTables, planSummary } from './planExport';

const g = (allergens: string[], diseases: string[], count: number, i = 0): DietGroup =>
  ({ label: `그룹 ${i + 1}`, allergens, diseases, count });

describe('제한 조건 그룹 검사', () => {
  it('조건 없이 인원만 넣은 그룹은 막고, 그룹 번호를 문구에 넣는다', () => {
    const groups = [g(['우유'], [], 5), g([], ['고혈압'], 3), g([], [], 4)];
    const p = groupProblems(groups, 100);
    expect(problemMessage(p)).toBe('그룹 3에 선택한 조건이 없습니다. 조건을 선택하거나 그룹을 삭제해 주세요.');
    expect([...problemRows(p)]).toEqual([2]);
  });

  it('여러 그룹이면 모두 적는다', () => {
    const p = groupProblems([g(['우유'], [], 5), g([], [], 1), g([], [], 2)], 100);
    expect(problemMessage(p)).toBe('그룹 2, 그룹 3에 선택한 조건이 없습니다. 조건을 선택하거나 그룹을 삭제해 주세요.');
  });

  it('손대지 않은 빈 그룹(조건 없음·0명)은 검사·전송에서 뺀다', () => {
    const groups = [g([], [], 0), g(['난류'], ['당뇨'], 2)];
    expect(problemMessage(groupProblems(groups, 100))).toBeNull();
    expect(toDietGroups(groups)).toEqual([{ label: '그룹 2', allergens: ['난류'], diseases: ['당뇨'], count: 2 }]);
  });

  it('조건만 고르고 인원이 0이면, 인원 합이 전체를 넘으면 막는다', () => {
    expect(problemMessage(groupProblems([g([], ['고혈압'], 0)], 100))).toBe('그룹 1의 인원을 입력해 주세요.');
    const over = groupProblems([g(['우유'], [], 60), g([], ['고혈압'], 55)], 100);
    expect(over.over).toBe(15);
    expect(problemMessage(over)).toContain('15명 많습니다');
    expect(problemRows(over).size).toBe(0);
  });
});

describe('복제 — 저장본 입력값 복원', () => {
  it('새 저장본(diseases 포함)은 그대로', () => {
    expect(groupsFromInputs({ inputGroups: [g(['우유'], ['고혈압'], 4)] })).toEqual([g(['우유'], ['고혈압'], 4)]);
  });
  it('예전 저장본: 알레르기 그룹 + 기저질환 인원 → 질환마다 그룹 하나(지원하지 않는 신장질환은 뺀다)', () => {
    const out = groupsFromInputs({
      inputGroups: [{ label: '그룹 1', allergens: ['난류'], count: 3 }],
      inputConds: { 고혈압: 5, 신장질환: 2 },
    });
    expect(out).toEqual([g(['난류'], [], 3, 0), g([], ['고혈압'], 5, 1)]);
  });
  it('입력값이 없으면 빈 그룹 하나', () => {
    expect(groupsFromInputs({})).toEqual([g([], [], 0)]);
  });
});

const n = (kcal: number, cost: number) => ({ kcal, protein: 10, sodium: 300, cost, cost_exact: cost });

// 2일 점심. 그룹 1 = 고혈압(4명) — 1일 김치찌개 → 미역국(나트륨 교체)·충족, 2일은 기준 초과.
function makePlan(): MealPlan {
  const raw = {
    status: 'FEASIBLE',
    applied_targets: { meals: ['점심'], target_kcal_per_day: 600, budget_per_meal: 4500, budget_mode: 'day' },
    plan: { 1: { 점심: ['밥', '김치찌개'] }, 2: { 점심: ['밥', '된장국'] } },
    plan_ids: { 1: { 점심: [1, 2] }, 2: { 점심: [1, 3] } },
    menu_nutrition_by_id: { 1: n(300, 500), 2: n(150, 900), 3: n(120, 700), 4: n(100, 600) },
    alternatives: [{
      group: { label: '그룹 1', allergens: [], count: 4 },
      kind: '기저질환', diseases: ['고혈압'], pending_diseases: ['신장질환'],
      limits: [{ key: 'sodium', label: '나트륨', cap: 2000, unit: 'mg', source: '대한고혈압학회 고혈압 진료지침' }],
      plan: { 1: { 점심: ['밥', '미역국'] }, 2: { 점심: ['밥', '된장국'] } },
      plan_ids: { 1: { 점심: [1, 4] }, 2: { 점심: [1, 3] } },
      disease_swaps: [{ day: 1, meal: '점심', category: '국', original: '김치찌개', alternative: '미역국', reduced: ['sodium'] }],
      day_report: {
        1: { status: '충족', limits: { sodium: { label: '나트륨', total: 1850, cap: 2000, unit: 'mg', ok: true } }, info: {}, unknown_menus: [] },
        2: { status: '확인 필요', limits: { sodium: { label: '나트륨', total: 2350, cap: 2000, unit: 'mg', ok: false } }, info: {}, unknown_menus: [] },
      },
      unmet_days: [2],
    }],
  } as unknown as MenuGenerateRaw;
  return toMealPlan(raw, {
    serving_count: 100, budget_limit_per_person: 4500,
    diet_groups: [{ label: '그룹 1', allergens: [], diseases: ['고혈압', '신장질환'], count: 4 }],
  } as MenuGenerateRequest);
}
const altDay = (p: MealPlan, di: number) => p.alternatives[0].weeks[0].days[di];

describe('기저질환 대체식 — 응답 매핑·검토 표시', () => {
  it('그룹 종류·기준·미반영 질환·하루 결과가 붙는다', () => {
    const plan = makePlan();
    const t = plan.alternatives[0];
    expect(t.kind).toBe('기저질환');
    expect(t.limits?.[0].cap).toBe(2000);
    expect(pendingText(t)).toBe('신장질환은 지원하지 않아 반영되지 않았습니다');
    expect(dietDayLines(altDay(plan, 0))).toEqual([{ key: 'sodium', label: '나트륨', text: '나트륨 1,850/2,000mg', ok: true }]);
    expect(dietSummary(t)).toEqual({ met: 1, total: 2, stale: 0 });
    expect(plan.conditionText).toContain('대체식 1그룹');
    expect(plan.checks.map((c) => c.label)).toContain("'그룹 1' 질환 기준 미충족 1일 확인");
  });

  it('질환 기준 때문에 바꾼 메뉴에만 사유가 붙는다', () => {
    const plan = makePlan();
    const items = altDay(plan, 0).cells[0].items;
    expect(dietSwapOf(items.find((x) => x.name === '미역국')!)).toBe('나트륨');
    expect(dietSwapOf(items.find((x) => x.name === '밥')!)).toBeUndefined();
  });

  it('검토에서 그날 메뉴를 바꾸면 재확인 필요가 되고 질환 대체 표시는 걷힌다', () => {
    let plan = makePlan();
    const it0 = altDay(plan, 0).cells[0].items.find((x) => x.name === '미역국')!;
    plan = editPlan(plan, it0, { kind: 'swap', to: { name: '콩나물국', nutritionId: 9, nutri: { kcal: 90, protein: 5, sodium: 400, cost: 500 } } });
    const d = altDay(plan, 0);
    expect(isDietStale(d)).toBe(true);
    expect(dietDayNote(d)).toBe('메뉴 수정됨 · 질환 기준 재확인 필요');
    expect(dietSwapOf(d.cells[0].items.find((x) => x.name === '콩나물국')!)).toBeUndefined();
    expect(dietSummary(plan.alternatives[0])).toEqual({ met: 0, total: 2, stale: 1 });
  });

  it('PDF — 제목에 충족 일수, 바꾼 메뉴는 [질환 대체], 못 맞춘 날은 ※ 로 실린다', () => {
    const plan = makePlan();
    const [table] = altTables(plan);
    expect(table.title).toContain('기저질환(고혈압)');
    expect(table.title).toContain('질환 기준 충족 1/2일');
    expect(String(table.rows[0][3])).toContain('[질환 대체] 미역국');
    expect(String(table.rows[1][3])).toContain('※ 질환 기준 확인 필요(나트륨 2,350/2,000mg)');
    expect(planSummary(plan).find((r) => r.label === '대체식 그룹')!.value).toBe('1그룹 · 4명 (기저질환 1)');
  });
});
