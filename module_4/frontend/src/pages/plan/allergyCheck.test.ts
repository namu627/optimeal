// 서버가 알레르기 대체를 못 한 접시(alternatives[].unresolved)가 대체식 칸에 '알레르기 확인'으로 남는지.
// 화면과 같은 경로: 생성 응답 → toMealPlan → 검토 편집(editPlan) → PDF 대체식 표(altTables).
import { describe, expect, it } from 'vitest';

import { toMealPlan, type MealPlan, type MenuGenerateRaw, type MenuGenerateRequest } from '../../api/menu';
import { allergyCheckText, editPlan } from './altSync';
import { altTables } from './planExport';

const n = (kcal: number, cost: number) => ({ kcal, protein: 10, sodium: 300, cost, cost_exact: cost });

// 1일 점심: [밥, 달걀볶음, 곤약 콩조림]. 난류·대두 그룹 — 달걀볶음은 안전한 대체 없음, 곤약 콩조림은 재료 정보 없음.
function makePlan(): MealPlan {
  const raw = {
    status: 'FEASIBLE',
    applied_targets: { meals: ['점심'], target_kcal_per_day: 600, budget_per_meal: 4500, budget_mode: 'day' },
    plan: { 1: { 점심: ['밥', '달걀볶음', '곤약 콩조림'] } },
    plan_ids: { 1: { 점심: [1, 2, 3] } },
    menu_nutrition_by_id: { 1: n(300, 500), 2: n(150, 900), 3: n(120, 700) },
    alternatives: [{
      group: { label: '그룹 1', allergens: ['난류', '대두'], count: 2 },
      plan: { 1: { 점심: ['밥', '달걀볶음', '곤약 콩조림'] } },
      plan_ids: { 1: { 점심: [1, 2, 3] } },
      unresolved: [
        { day: 1, meal: '점심', original: '달걀볶음', hit_allergens: ['난류'], reason: '안전한 대체 메뉴 없음' },
        { day: 1, meal: '점심', original: '곤약 콩조림', hit_allergens: [], reason: '재료 정보 없음 — 알레르기 판정 불가' },
      ],
    }],
  } as unknown as MenuGenerateRaw;
  return toMealPlan(raw, { serving_count: 100, budget_limit_per_person: 4500 } as MenuGenerateRequest);
}
const altCell = (p: MealPlan) => p.alternatives[0].weeks[0].days[0].cells[0];
const altItem = (p: MealPlan, name: string) => altCell(p).items.find((x) => x.name === name)!;
const mainItem = (p: MealPlan, name: string) => p.weeks[0].days[0].cells[0].items.find((x) => x.name === name)!;
const to = (name: string, nutritionId: number) => ({ name, nutritionId, nutri: { kcal: 120, protein: 9, sodium: 250, cost: 800 } });

describe('알레르기 대체 못 한 접시 표시', () => {
  it('대체식 칸 메뉴에만 사유가 붙고, 일반식에는 없다', () => {
    const plan = makePlan();
    expect(altItem(plan, '달걀볶음').allergyCheck).toBe('난류 · 안전한 대체 메뉴 없음');
    expect(altItem(plan, '곤약 콩조림').allergyCheck).toBe('재료 정보 없음 — 알레르기 판정 불가');
    expect(altItem(plan, '밥').allergyCheck).toBeUndefined();
    expect(mainItem(plan, '달걀볶음').allergyCheck).toBeUndefined();
  });

  it('PDF 대체식 표에 바뀐 메뉴가 없어도 그 끼니가 실린다', () => {
    const [table] = altTables(makePlan());
    expect(table.rows).toHaveLength(1);
    expect(String(table.rows[0][3])).toContain('알레르기 확인 필요(달걀볶음: 난류 · 안전한 대체 메뉴 없음 / 곤약 콩조림: 재료 정보 없음');
  });

  it('대체식 칸에서 직접 교체하면 걷히고, 되돌리면 다시 붙는다', () => {
    let plan = makePlan();
    plan = editPlan(plan, altItem(plan, '달걀볶음'), { kind: 'swap', to: to('시금치나물', 40) });
    expect(altItem(plan, '시금치나물').allergyCheck).toBeUndefined();
    expect(allergyCheckText(altCell(plan))).not.toContain('달걀볶음');
    plan = editPlan(plan, altItem(plan, '시금치나물'), { kind: 'revert' });
    expect(altItem(plan, '달걀볶음').allergyCheck).toBe('난류 · 안전한 대체 메뉴 없음');
  });

  it('일반식 교체를 따라간 메뉴는 알레르겐을 확인하지 않았으므로 표시를 유지한다', () => {
    let plan = makePlan();
    plan = editPlan(plan, mainItem(plan, '곤약 콩조림'), { kind: 'swap', to: to('두부조림', 50) });
    expect(altItem(plan, '두부조림').allergyCheck).toBe('재료 정보 없음 — 알레르기 판정 불가');
  });
});
