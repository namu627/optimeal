// 검토에서 메뉴를 교체한 뒤 PDF 요청 본문(buildPdfRequest = downloadPlanPdf 가 서버에 보내는 그대로)에 새 메뉴가 들어가는지.
// 화면과 같은 경로: 생성 응답 → toMealPlan → 검토 편집(editPlan) → 확정 PDF 본문.
// 레시피 서버 조회(fetchMenuRecipes)만 가짜로 바꾼다 — 교체한 메뉴는 응답에 레시피가 없어 서버에서 조회해야 한다.
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../api/menu', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../../api/menu')>();
  return {
    ...orig,
    fetchMenuRecipes: vi.fn(async (ids: number[], names: string[]) => ({
      by_id: Object.fromEntries(ids.map((id) => [String(id), { ingredients: [{ name: `재료${id}`, amount: 100 }], steps: [`조리${id}`] }])),
      by_name: Object.fromEntries(names.map((n) => [n, { ingredients: [{ name: `재료-${n}`, amount: 50 }], steps: [] }])),
    })),
  };
});

import { fetchMenuRecipes, toMealPlan, type MealPlan, type MenuGenerateRaw, type MenuGenerateRequest } from '../../api/menu';
import { editPlan } from './altSync';
import { buildPdfRequest, tableRows } from './planExport';

const n = (kcal: number, cost: number) => ({ kcal, protein: 10, sodium: 300, cost, cost_exact: cost });
const rec = (name: string) => ({ ingredients: [{ name: `${name} 재료`, amount: 1000 }], steps: [`${name} 만들기`] });

// 1일 점심: [밥, 미역국, 달걀찜]. 그룹 1(난류)은 달걀찜 → 두부조림으로 알레르기 대체.
function makePlan(): MealPlan {
  const raw = {
    status: 'FEASIBLE',
    applied_targets: { meals: ['점심'], target_kcal_per_day: 600, budget_per_meal: 4500, budget_mode: 'day' },
    plan: { 1: { 점심: ['밥', '미역국', '달걀찜'] } },
    plan_ids: { 1: { 점심: [1, 2, 3] } },
    menu_nutrition_by_id: { 1: n(300, 500), 2: n(100, 800), 3: n(150, 1200) },
    menu_nutrition: { 밥: n(300, 500), 미역국: n(100, 800), 달걀찜: n(150, 1200), 두부조림: n(140, 1000) },
    menu_recipes_by_id: { 1: rec('밥'), 2: rec('미역국'), 3: rec('달걀찜') },
    alternatives: [{ group: { label: '그룹 1', allergens: ['난류'], count: 3 }, plan: { 1: { 점심: ['밥', '미역국', '두부조림'] } } }],
  } as unknown as MenuGenerateRaw;
  return toMealPlan(raw, { serving_count: 100, budget_limit_per_person: 4500 } as MenuGenerateRequest);
}
const mainItem = (p: MealPlan, name: string) => p.weeks[0].days[0].cells[0].items.find((x) => x.name === name)!;
const swap = (p: MealPlan, from: string, name: string, nutritionId: number) =>
  editPlan(p, mainItem(p, from), { kind: 'swap', to: { name, nutritionId, nutri: { kcal: 120, protein: 9, sodium: 250, cost: 900 } } });

describe('교체 후 PDF 요청 본문', () => {
  it('일반식 식단표·조리 지시서·대체식 표·대체식 조리 지시서에 교체가 반영된다', async () => {
    let plan = makePlan();
    plan = swap(plan, '미역국', '콩나물국', 20);   // 대체되지 않은 자리 → 대체식도 따라감
    plan = swap(plan, '달걀찜', '감자조림', 30);   // 알레르기 대체 자리 → 대체식 유지 + 재검토 필요

    const body = await buildPdfRequest(plan, '테스트', { recipes: true, alternatives: true });

    const cell = body.grids[0].rows[0].cells[0]!;
    expect(cell.menus).toEqual(['밥', '콩나물국', '감자조림']);
    const recipes = body.recipes.map((r) => r.name);
    expect(recipes).toEqual(['밥', '콩나물국', '감자조림']);
    // 교체한 메뉴는 응답에 레시피가 없어 서버에서 id 로 조회해 넣는다(기존 규칙).
    expect(vi.mocked(fetchMenuRecipes)).toHaveBeenCalledWith(expect.arrayContaining([20, 30]), [], 100);
    expect(body.recipes.find((r) => r.name === '콩나물국')!.steps).toEqual(['조리20']);

    const [table] = body.tables;
    expect(table.rows).toHaveLength(1);
    const [, , mainCol, altCol] = table.rows[0] as string[];
    expect(mainCol).toBe('밥, 콩나물국, 감자조림');
    expect(altCol).toContain('밥, 콩나물국, [대체] 두부조림');      // 따라간 메뉴 + 알레르기 대체 메뉴 유지
    expect(altCol).not.toMatch(/미역국,|미역국$/);                  // 교체 전 메뉴는 대체식 칸에서도 사라짐
    expect(altCol).toContain("대체식 재검토 필요(일반식 '달걀찜' → '감자조림' 교체)");
    expect(altCol).toContain("일반식 교체 반영(일반식 '미역국' → '콩나물국' 교체)");
    expect(table.title).toContain('바뀐 끼니 1개 · 일반식 교체 반영 1개');
    expect(body.recipe_sections?.[0].recipes.map((r) => r.name)).toEqual(['두부조림']);

    // 식단표 CSV 도 같은 칸
    expect(tableRows(plan)[1][4]).toBe('밥 콩나물국 감자조림');
  });

  it('되돌리기·삭제도 같은 규칙: 되돌리면 표시가 걷히고, 대체 자리를 삭제하면 재검토 필요', async () => {
    let plan = makePlan();
    plan = swap(plan, '미역국', '콩나물국', 20);
    plan = editPlan(plan, mainItem(plan, '콩나물국'), { kind: 'revert' });
    plan = editPlan(plan, mainItem(plan, '달걀찜'), { kind: 'delete' });

    const alt = plan.alternatives[0].weeks[0].days[0].cells[0];
    expect(alt.items.map((x) => x.name)).toEqual(['밥', '미역국', '두부조림']);
    expect(alt.altFollow).toBeUndefined();
    expect(alt.altReview?.map((r) => r.note)).toEqual(["일반식 '달걀찜' 삭제"]);

    const body = await buildPdfRequest(plan, '테스트', { recipes: true, alternatives: true });
    expect(body.grids[0].rows[0].cells[0]!.menus).toEqual(['밥', '미역국']);
    expect(body.recipes.map((r) => r.name)).toEqual(['밥', '미역국']);
    expect((body.tables[0].rows[0] as string[])[3]).toContain('대체식 재검토 필요');
  });
});
