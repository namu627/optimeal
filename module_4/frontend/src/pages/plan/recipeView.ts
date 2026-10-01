// src/pages/plan/recipeView.ts
// 레시피 화면(RecipeDrawer)의 데이터 로직 — 식단에서 메뉴를 모으고 레시피를 찾는다(UI 없음).
import {
  MEAL_TABLE, fetchMenuRecipes, type MealPlan, type MealKind, type MenuRecipe, type RecipeIngredient, type WeekBlock,
} from '../../api/menu';

// 한 트랙(일반식·대체식) 안에서 같은 메뉴는 한 번만 — 솔버가 고른 행(nutritionId)이 있으면 그걸로 구분.
export interface RecipeEntry { key: string; name: string; nutritionId?: number; uses: string[] }
export type FetchedRecipes = { by_id: Record<string, MenuRecipe>; by_name: Record<string, MenuRecipe> };

export function collectEntries(weeks: WeekBlock[]): RecipeEntry[] {
  const map = new Map<string, RecipeEntry>();
  weeks.forEach((wk) => wk.days.forEach((d) => d.cells.forEach((c) => c.items.forEach((it) => {
    const key = it.nutritionId != null ? `id:${it.nutritionId}` : `name:${it.name}`;
    const use = `${d.date} ${MEAL_TABLE[c.kind as MealKind]}`;
    const e = map.get(key);
    if (e) e.uses.push(use);
    else map.set(key, { key, name: it.name, nutritionId: it.nutritionId, uses: [use] });
  }))));
  return [...map.values()];
}

// 생성 응답(저장본 포함)에 실린 레시피. steps 키가 없는 건 이 화면 이전 저장본이라 다시 조회한다.
export function storedRecipe(plan: MealPlan, e: RecipeEntry): MenuRecipe | undefined {
  const rec = e.nutritionId != null ? plan.menuRecipesById?.[String(e.nutritionId)] : plan.menuRecipes?.[e.name];
  return rec && Array.isArray(rec.steps) ? rec : undefined;
}

export function fetchedRecipe(f: FetchedRecipes, e: RecipeEntry): MenuRecipe | undefined {
  return e.nutritionId != null ? f.by_id[String(e.nutritionId)] : f.by_name[e.name];
}

/** 응답에 없던 메뉴(교체한 메뉴·대체식·구버전 저장본) — 서버에 조회할 id·이름. */
export function missingRecipes(plan: MealPlan, entries: RecipeEntry[]): { ids: number[]; names: string[] } {
  const ids = [...new Set(entries.filter((e) => e.nutritionId != null && !storedRecipe(plan, e)).map((e) => e.nutritionId!))];
  const names = [...new Set(entries.filter((e) => e.nutritionId == null && !storedRecipe(plan, e)).map((e) => e.name))];
  return { ids, names };
}

const CHUNK = 100; // 백엔드 /api/menu/recipes 한 번 조회 상한

/** missingRecipes 결과를 100개씩 나눠 조회해 합친다(31일 식단·구버전 저장본 대비). */
export async function fetchMissingRecipes(need: { ids: number[]; names: string[] }, servings: number): Promise<FetchedRecipes> {
  const jobs: Promise<FetchedRecipes>[] = [];
  for (let i = 0; i < need.ids.length; i += CHUNK) jobs.push(fetchMenuRecipes(need.ids.slice(i, i + CHUNK), [], servings));
  for (let i = 0; i < need.names.length; i += CHUNK) jobs.push(fetchMenuRecipes([], need.names.slice(i, i + CHUNK), servings));
  const res = await Promise.all(jobs);
  return res.reduce<FetchedRecipes>((acc, r) => ({ by_id: { ...acc.by_id, ...r.by_id }, by_name: { ...acc.by_name, ...r.by_name } }),
    { by_id: {}, by_name: {} });
}

/* ── 투입량(총량) 기준 — 레시피 화면·CSV·PDF 가 모두 이 두 함수로 값과 기준을 적는다 ──
   스케일링: 업장 캘리브레이션 보정이 있는 재료(est_ratio × 1인분 × 인원). 단순 비례: 1인분 × 인원.
   basis 필드가 없는 저장본은 생성 당시 단순 비례로 계산된 값이다. */
export const BASIS_LINEAR = '단순 비례';
export function amountBasis(ing: RecipeIngredient): string {
  return ing.amount == null ? '' : ing.basis ?? BASIS_LINEAR;
}
/** 1인분(g). 스케일링 총량은 1인분×인원이 아니므로 base_g 를 먼저 쓴다(구저장본만 총량÷인원). */
export function perServing(ing: RecipeIngredient, headcount: number): number | null {
  if (ing.base_g != null) return ing.base_g;
  return ing.amount == null ? null : ing.amount / headcount;
}
