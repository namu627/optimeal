// src/pages/plan/recipeView.ts
// 레시피 화면(RecipeDrawer)의 데이터 로직 — 식단에서 메뉴를 모으고 레시피를 찾는다(UI 없음).
import {
  MEAL_TABLE, fetchMenuRecipes, type MealPlan, type MealKind, type MenuRecipe, type RecipeIngredient, type WeekBlock,
} from '../../api/menu';

// 한 트랙(일반식·대체식) 안에서 같은 메뉴는 한 번만 — 솔버가 고른 행(nutritionId)이 있으면 그걸로 구분.
export interface RecipeEntry { key: string; name: string; nutritionId?: number; uses: string[] }
export type FetchedRecipes = { by_id: Record<string, MenuRecipe>; by_name: Record<string, MenuRecipe> };

/** 칸 메뉴 → 엔트리 키. 재료 치환 접시('달걀찜(대체: 달걀→두부)')는 id 가 원래 메뉴 것이라 이름까지 붙여
 *  원래 메뉴·다른 치환(그룹마다 대체 재료가 다를 수 있다)과 섞이지 않게 한다. */
export function entryKey(it: { name: string; nutritionId?: number }): string {
  if (it.nutritionId == null) return `name:${it.name}`;
  return parseSubstitution(it.name).length ? `id:${it.nutritionId}:${it.name}` : `id:${it.nutritionId}`;
}

export function collectEntries(weeks: WeekBlock[]): RecipeEntry[] {
  const map = new Map<string, RecipeEntry>();
  weeks.forEach((wk) => wk.days.forEach((d) => d.cells.forEach((c) => c.items.forEach((it) => {
    const key = entryKey(it);
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

/** entries 의 레시피를 한 번에 찾는 함수 — 응답·저장본에 있으면 그것, 없으면 서버 조회. CSV·PDF 조리 지시서 공통. */
export async function resolveRecipes(plan: MealPlan, entries: RecipeEntry[]): Promise<(e: RecipeEntry) => MenuRecipe | undefined> {
  const need = missingRecipes(plan, entries);
  let fetched: FetchedRecipes = { by_id: {}, by_name: {} };
  if (need.ids.length || need.names.length) fetched = await fetchMissingRecipes(need, plan.headcount);
  return (e) => substitutedRecipe(storedRecipe(plan, e) ?? fetchedRecipe(fetched, e), e.name);
}

/* ── 재료 치환 대체식 ──
   module_3 alternative_menu 의 재료 치환은 메뉴 이름에 '(대체: 달걀→두부, 우유→두유)' 만 붙이고 id 는 원래 메뉴 것을
   쓴다(backend _alt_plan_ids ②). 그래서 레시피는 원래 메뉴 그대로 와서 재료·조리 순서에 알레르겐이 남았다.
   레시피 화면·CSV·PDF 가 모두 이 함수를 거쳐 치환을 반영한다. 치환 이름은 재료 목록과 같은 ingredient_name 이다.
   ※ 2026-10-06 부터 생성은 재료 치환을 쓰지 않는다(backend _derive_alternatives) — 이 처리는 그 전 저장본용. */
const SUB_RE = /\(대체: ([^)]+)\)\s*$/;

/** '메뉴(대체: 달걀→두부, 우유→두유)' → [['달걀','두부'], ['우유','두유']]. 치환 접시가 아니면 []. */
export function parseSubstitution(name: string): [string, string][] {
  const m = SUB_RE.exec(name);
  if (!m) return [];
  return m[1].split(',')
    .map((s) => s.split('→').map((x) => x.trim()))
    .filter((p): p is [string, string] => p.length === 2 && !!p[0] && !!p[1]);
}

// 조리 순서 원문에서 치환 재료를 가리키는 표기. 재료 목록은 '달걀'인데 원문은 '계란'인 경우가 많다.
// '콩'·'밀'처럼 다른 낱말(콩나물·밀가루 등) 안에 들어가는 이름은 원문 치환에서 뺀다 — 경고 문구로만 알린다.
const STEP_ALIASES: Record<string, string[]> = {
  달걀: ['달걀', '계란'], 계란: ['달걀', '계란'], 난류: ['달걀', '계란'],
  우유: ['우유'], 대두: ['대두'], 돼지고기: ['돼지고기'], 밀가루: ['밀가루'], 밀: ['밀가루'],
};

export const substituteMarker = (to: string, from: string) => `${to}(${from} 대신)`;

/** 치환 접시면 재료명·조리 순서의 원래 재료를 대체 재료로 바꾼 사본을, 아니면 rec 그대로 돌려준다.
 *  투입량은 원래 재료 분량 그대로라 조리 순서 첫 줄에 확인 문구를 붙인다. */
export function substitutedRecipe(rec: MenuRecipe | undefined, name: string): MenuRecipe | undefined {
  const swaps = parseSubstitution(name);
  if (!rec || !swaps.length) return rec;
  const to = new Map(swaps);
  const ingredients = rec.ingredients.map((ing) => {
    const sub = to.get(ing.name);
    return sub ? { ...ing, name: substituteMarker(sub, ing.name) } : ing;
  });
  let steps = rec.steps;
  if (steps?.length) {
    // 한 번에 바꾼다(긴 표기 먼저) — 앞 치환이 남긴 '(우유 대신)' 같은 표시를 다음 치환이 다시 건드리지 않게.
    const aliasTo = new Map(swaps.flatMap(([from, sub]) => (STEP_ALIASES[from] ?? []).map((a) => [a, sub] as const)));
    if (aliasTo.size) {
      const re = new RegExp([...aliasTo.keys()].sort((a, b) => b.length - a.length).join('|'), 'g');
      steps = steps.map((s) => s.replace(re, (a) => substituteMarker(aliasTo.get(a)!, a)));
    }
    const txt = swaps.map(([from, sub]) => `${from}→${sub}`).join(', ');
    steps = [`※ 알레르기 재료 치환(${txt}) — 원문에서 재료 이름만 바꿨고 분량은 원래 재료 기준이에요. 조리법·분량은 영양사 확인이 필요해요.`, ...steps];
  }
  return { ...rec, ingredients, steps };
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

/** 그룹별 대체식에서 바뀐 메뉴만 남긴 weeks — 대체식 조리 지시서(CSV·PDF)의 메뉴 집합은 이것 하나에서 나온다. */
export function changedAltTracks(plan: MealPlan): { label: string; weeks: WeekBlock[] }[] {
  return plan.alternatives.map((t) => ({
    label: t.label,
    weeks: t.weeks.map((wk, w) => ({ ...wk, days: wk.days.map((d, di) => ({ ...d, cells: d.cells.map((c) => {
      const changed = changedNames(plan.weeks, t.weeks, w, di, c.kind);
      return { ...c, items: c.items.filter((i) => changed.has(i.name)) };
    }) })) })),
  }));
}

/** 대체식 칸에서 일반식 같은 칸(같은 주·날·끼니)에 없는 메뉴 이름 — '바뀐 메뉴'(알레르기 대체).
 *  일반식 교체는 대체식에도 전파되지만(altSync), 전파 이전 저장본은 대체식 트랙에 교체 전 메뉴가 남아 있다 —
 *  그건 알레르기 대체가 아니므로 일반식 칸의 교체 전 이름(orig)도 일반식 메뉴로 친다. */
export function changedNames(main: WeekBlock[], alt: WeekBlock[], w: number, d: number, kind: MealKind): Set<string> {
  const mainCell = main[w]?.days[d]?.cells.find((c) => c.kind === kind);
  const altCell = alt[w]?.days[d]?.cells.find((c) => c.kind === kind);
  const base = new Set((mainCell?.items ?? []).flatMap((i) => (i.orig ? [i.name, i.orig] : [i.name])));
  return new Set((altCell?.items ?? []).map((i) => i.name).filter((n) => !base.has(n)));
}
