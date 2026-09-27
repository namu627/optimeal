// src/api/menu.ts
// 식단 생성(모듈 3 CSP) API 래퍼 + 뷰모델 + 목업.
// 백엔드 generate 가 503(CSP 미통합)인 동안 UI 는 뷰모델(MealPlan)만 바인딩한다.
// 통합되면 toMealPlan() 한 함수만 실제 응답에 맞춰 채우면 됨.
import { api } from './client';

/* ────────────── 백엔드 계약 (Swagger) ────────────── */
// 백엔드 schemas.UserProfileOut 과 동일. 영양 수치는 전부 **1일** 기준(legal_meal_* 만 1식).
export interface MenuProfile {
  profile_key: string; group_name: string; group_type: string; sex: string; age_band: string;
  daily_kcal: number; protein_g?: number | null;
  sodium_cdrr_mg?: number | null; sodium_ai_mg?: number | null;
  legal_meal_kcal?: number | null; default_meals: number;
  source: string; note?: string;
}
export interface AllergyGroup { label: string; allergens: string[]; count: number; }
export interface MenuGenerateRequest {
  profile_key?: string;
  serving_count?: number;
  days?: number;                // 7 | 31
  meals?: string[];
  target_kcal_per_day?: number;
  sodium_max_mg_per_day?: number | null;
  budget_limit_per_person?: number | null;
  with_alternatives?: boolean;
  allergy_groups?: AllergyGroup[];
  conditions?: string[];
  exclude_menu_ids?: number[];
  include_menu_ids?: number[];
}
export interface MenuGenerateRaw { status?: string; plan?: unknown; [k: string]: unknown; }

export async function listProfiles(): Promise<MenuProfile[]> {
  const { data } = await api.get<MenuProfile[]>('/api/menu/profiles'); return data;
}
// 예산 단위: 화면·요청 객체(MenuGenerateRequest)의 budget_limit_per_person 은 "1인 1식"(한 끼) 값이다.
// 백엔드 /generate 는 이 값을 **하루 상한**(budget_period='day')으로 해석하므로, 실제로 보내는 본문에서만
// 한 끼 예산 × 끼니 수로 바꾼다. 끼니 1개(점심만)면 ×1 이라 기존과 같다.
// 셀 원가(한 끼)·경고는 toMealPlan 이 요청 객체의 한 끼 값과 비교한다(변환 전 값).
export function toWireRequest(body: MenuGenerateRequest): MenuGenerateRequest {
  const perMeal = body.budget_limit_per_person;
  if (perMeal == null) return body;
  const nMeals = Math.max(1, body.meals?.length ?? 1);
  return { ...body, budget_limit_per_person: perMeal * nMeals };
}
export async function generateMenu(body: MenuGenerateRequest): Promise<MenuGenerateRaw> {
  const { data } = await api.post<MenuGenerateRaw>('/api/menu/generate', toWireRequest(body)); return data;
}
export function isUnavailable(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 503;
}
// 서버가 조건을 실제로 풀었으나 해가 없는 응답(INFEASIBLE 등)인지 — 목업으로 감추지 말고
// 조건 충돌 화면으로 보내야 하는 경우. status 가 OPTIMAL/FEASIBLE 이 아니거나 plan 이 비면 true.
export function isInfeasibleResponse(raw: MenuGenerateRaw | null | undefined): boolean {
  if (!raw) return true;
  if (raw.status && raw.status !== 'OPTIMAL' && raw.status !== 'FEASIBLE') return true;
  const plan = raw.plan as Record<string, unknown> | null | undefined;
  return !plan || !Object.keys(plan).length;
}

/* ────────────── 저장된 식단 (/api/menu/plans) ──────────────
   MealPlan 뷰모델을 그대로 저장·반환한다. 로그인 체계가 없어 소유자 없는 전역 목록(MVP). */
export interface SavedPlanSummary {
  id: number; name: string; created_at: string;
  headcount: number | null; total_days: number | null;
  cost_per_person: number | null; budget_per_person: number | null;
  condition_text: string | null; period_text: string | null; start_date: string | null;
}
export interface SavedPlan { id: number; name: string; created_at: string; plan: MealPlan }

export async function savePlan(name: string, plan: MealPlan): Promise<{ id: number }> {
  const { data } = await api.post<{ id: number }>('/api/menu/plans', { name, plan }); return data;
}
export async function listSavedPlans(limit = 50): Promise<SavedPlanSummary[]> {
  const { data } = await api.get<SavedPlanSummary[]>('/api/menu/plans', { params: { limit } }); return data;
}
export async function getSavedPlan(id: number): Promise<SavedPlan> {
  const { data } = await api.get<SavedPlan>(`/api/menu/plans/${id}`); return data;
}
export async function deleteSavedPlan(id: number): Promise<void> {
  await api.delete(`/api/menu/plans/${id}`);
}
// 저장 시각(ISO, UTC) → 로컬 표기. 예: '9월 26일 오전 01:03'
export function formatSavedAt(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleString('ko-KR', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/* ────────────── 뷰모델 ────────────── */
export type MealKind = 'breakfast' | 'lunch' | 'dinner';
export const MEAL_KR: Record<MealKind, string> = { breakfast: '아침', lunch: '점심', dinner: '저녁' };
export const MEAL_TABLE: Record<MealKind, string> = { breakfast: '조식', lunch: '중식', dinner: '석식' };
export const MEAL_TIME: Record<MealKind, string> = { breakfast: '08:00', lunch: '12:20', dinner: '17:40' };

export type CellFlag = '나트륨' | '원가';
// menuId: 칸별 편집 식별용 순번(같은 메뉴가 여러 날 나와도 칸마다 다름) — 교체·삭제는 이걸로 찾는다.
// nutritionId: 솔버가 실제로 고른 행(nutrition_recipe.nutrition_id, 응답 plan_ids). 동명 메뉴 구분·
//   id 기반 영양/레시피 조회용. 대체식 칸·구버전 저장본·목업에는 없을 수 있다(optional).
// nutri: 이 칸 메뉴의 1인분 영양·원가 — 교체·삭제 후 셀·총합을 로컬에서 다시 계산할 때 쓴다(recomputePlan).
// orig*: 교체 전 원래 메뉴(되돌리기용). 목업·구버전 저장본에는 nutri 가 없다 → 그 셀은 재계산하지 않는다.
export interface ItemNutri { kcal: number; protein: number | null; sodium: number | null; cost: number | null }
export interface MealItem {
  menuId?: number; nutritionId?: number; name: string; flag?: CellFlag; alt?: boolean;
  nutri?: ItemNutri; orig?: string; origNutritionId?: number; origNutri?: ItemNutri;
}
export interface MealCell { kind: MealKind; items: MealItem[]; kcal: number; protein: number; warn?: boolean; }
export interface MealDay { date: string; dow: string; cells: MealCell[] }
export interface WeekBlock { label: string; days: MealDay[] }
export interface AltTrack { label: string; count: number; weeks: WeekBlock[] }
// 박미연 KpiRow 계약: 퍼센트가 아니라 목표 대비 실제값 쌍
export interface MetricValue { value: number; target: number; unit?: string }
export interface Achievement { calories: MetricValue; protein: MetricValue; sodium: MetricValue }
export interface PlanCheck { label: string; done: boolean; view?: boolean }

// Step3 조리 지시서용 — 메뉴명 → 재료 투입량(총량)·조리순서. 백엔드가 실제로 계산해 준
// 값(recipe_ingredient_map 보강분)만 들어오며, 레시피 미보강 메뉴는 note만 채워져 온다.
export interface RecipeIngredient { step?: number | null; name: string; amount?: number | null; unit?: string; role?: string | null }
export interface MenuRecipe { cooking_method?: string | null; ingredients: RecipeIngredient[]; note?: string | null }

export interface MealPlan {
  conditionText: string; periodText: string; headcount: number;
  meals: MealKind[]; weeks: WeekBlock[]; alternatives: AltTrack[];
  achievement: Achievement; costPerPerson: number; budgetPerPerson: number;
  totalDays: number; totalCost: number;
  /** 1일 나트륨 상한(mg, 백엔드 적용값). 셀 나트륨 경고(상한÷끼니 수) 재계산용. 없으면 경고 안 함. */
  sodiumCapPerDay?: number | null;
  checks: PlanCheck[]; rationale: string[]; source: 'live' | 'mock';
  menuRecipes?: Record<string, MenuRecipe>;
  /** nutrition_id(문자열 키) → 레시피. 있으면 MealItem.nutritionId 로 먼저 찾는다(동명 메뉴 정확). */
  menuRecipesById?: Record<string, MenuRecipe>;
}

/* ────────────── 실제 /api/menu/generate 응답 → 뷰모델 매핑 ──────────────
   plan 은 {일: {끼니(한글): [메뉴명,...]}}, 열량은 daily_kcal(하루 총합)만 온다.
   plan_ids 는 같은 모양의 {일: {끼니: [nutrition_id,...]}}(솔버가 고른 행). 있으면 메뉴별
   열량·단백질·나트륨·원가를 menu_nutrition_by_id 에서, 없으면(구버전 백엔드) menu_nutrition(이름)에서 채운다. */
interface MenuNutri { kcal?: number; protein?: number | null; sodium?: number | null; cost?: number | null }
type PlanIds = Record<string, Record<string, number[]>>;
interface GenerateResponse {
  status?: string;
  applied_targets?: {
    meals?: string[]; target_kcal_per_day?: number; sodium_max_mg_per_day?: number | null;
    protein_g?: number | null; // 프로파일 기준 단백질 목표(끼니 수 반영). 프로파일 미지정 시 null
  };
  plan?: Record<string, Record<string, string[]>>;
  daily_kcal?: Record<string, number>;
  total_cost_won?: number;
  menu_nutrition?: Record<string, MenuNutri>;
  menu_recipes?: Record<string, MenuRecipe>;
  plan_ids?: PlanIds | null;
  menu_nutrition_by_id?: Record<string, MenuNutri & { name?: string }>;
  menu_recipes_by_id?: Record<string, MenuRecipe>;
  alternatives?: Array<{
    group?: { label?: string; allergens?: string[]; count?: number };
    plan?: Record<string, Record<string, string[]>>;
  }>;
}
const MEAL_FROM_KR: Record<string, MealKind> = { 아침: 'breakfast', 점심: 'lunch', 저녁: 'dinner' };
const MEAL_ORDER = ['아침', '점심', '저녁'];

export function toMealPlan(raw: MenuGenerateRaw, req: MenuGenerateRequest): MealPlan {
  const r = raw as unknown as GenerateResponse;
  const plan = r.plan;
  if (!plan || !Object.keys(plan).length) throw new Error('toMealPlan: 응답에 plan 이 없음(503/INFEASIBLE)');

  const nutri = r.menu_nutrition ?? {};
  const nutriById = r.menu_nutrition_by_id ?? {};
  const planIds = r.plan_ids ?? null;
  // 한 자리의 영양값: 솔버가 고른 행(id)이 있으면 그것, 없으면 이름으로(대체식 칸·구버전 응답).
  const nutriOf = (name: string, id?: number): MenuNutri =>
    (id != null ? nutriById[String(id)] : undefined) ?? nutri[name] ?? {};
  const budget = req.budget_limit_per_person ?? 4500;
  const headcount = req.serving_count ?? 320;

  // 응답에 등장하는 끼니를 표준 순서(아침→점심→저녁)로 정렬해 MealKind 로 변환.
  const mealKr = (r.applied_targets?.meals?.length ? r.applied_targets.meals
    : Array.from(new Set(Object.values(plan).flatMap((d) => Object.keys(d)))));
  const meals: MealKind[] = MEAL_ORDER.filter((m) => mealKr.includes(m)).map((m) => MEAL_FROM_KR[m]);

  const sodiumTarget = r.applied_targets?.sodium_max_mg_per_day ?? req.sodium_max_mg_per_day ?? null;

  // 칸마다 1인분 영양·원가(nutri)만 싣는다. 셀 합계·경고·달성률·원가는 recomputePlan 이 계산한다
  // (생성 직후와 교체·삭제 후가 같은 계산을 쓰도록).
  const buildCell = (kind: MealKind, names: string[], alt: boolean, ids?: number[]): MealCell => {
    const items: MealItem[] = names.map((name, i) => {
      const nutritionId = ids?.[i]; // plan_ids 는 plan 과 같은 위치
      const n = nutriOf(name, nutritionId);
      const itemNutri: ItemNutri = { kcal: n.kcal ?? 0, protein: n.protein ?? null, sodium: n.sodium ?? null, cost: n.cost ?? null };
      return { menuId: ++_mid, nutritionId, name, alt: alt || undefined, nutri: itemNutri };
    });
    return { kind, items, kcal: 0, protein: 0 };
  };

  // idsObj: plan_ids(본식단만). 대체식 plan 은 아직 이름만 온다(alternative_menu id화는 별도 단계).
  const weeksFrom = (planObj: Record<string, Record<string, string[]>>, alt: boolean, idsObj?: PlanIds | null): WeekBlock[] => {
    const keys = Object.keys(planObj).sort((a, b) => Number(a) - Number(b));
    const blocks: WeekBlock[] = [];
    keys.forEach((dayKey, i) => {
      const { label, dow, week } = dateFor(i);
      if (!blocks[week]) blocks[week] = { label: `${week + 1}주차`, days: [] };
      const dayPlan = planObj[dayKey] ?? {};
      const cells = meals.map((k) => {
        const kr = MEAL_ORDER.find((o) => MEAL_FROM_KR[o] === k)!;
        return buildCell(k, dayPlan[kr] ?? [], alt, idsObj?.[dayKey]?.[kr]);
      });
      blocks[week].days.push({ date: label, dow, cells });
    });
    return blocks.filter(Boolean);
  };

  _mid = 0;
  _start = firstWeekday(new Date());
  const weeks = weeksFrom(plan, false, planIds);
  const alternatives: AltTrack[] = (r.alternatives ?? []).map((a) => ({
    label: a.group?.label ?? '대체식', count: a.group?.count ?? 0, weeks: weeksFrom(a.plan ?? {}, true),
  }));

  // 목표값만 여기서 정한다(값은 recomputePlan 이 칸 영양으로 채움).
  // 칸 영양 합 = 솔버 daily_kcal·total_cost_won 이다(plan_ids 로 솔버가 고른 행을 쓰므로).
  const days = Object.keys(plan).length;
  const kcalTarget = r.applied_targets?.target_kcal_per_day ?? req.target_kcal_per_day ?? 1;
  // 단백질 목표: 백엔드가 프로파일에서 산출한 값. 프로파일 없이 요청한 경우에만 열량의 15%(4kcal/g)로 근사.
  const proteinTarget = r.applied_targets?.protein_g ?? Math.max(1, Math.round((kcalTarget * 0.15) / 4));
  const achievement: Achievement = {
    calories: { value: 0, target: Math.round(kcalTarget), unit: 'kcal' },
    protein: { value: 0, target: proteinTarget, unit: 'g' },
    sodium: { value: 0, target: Math.round(sodiumTarget ?? 0), unit: 'mg' },
  };

  const profileLabel = PROFILE_LABEL[req.profile_key ?? ''] ?? (req.profile_key ?? '대상');
  const mealsText = meals.map((m) => MEAL_TABLE[m]).join('·');
  const groups = req.allergy_groups?.length ?? 0;
  const checks: PlanCheck[] = alternatives.map((t) => ({ label: `대체식 '${t.label}' 검토`, done: false, view: true }));

  return recomputePlan({
    conditionText: `${profileLabel} · ${headcount}명 · 평일 ${days}일 · ${mealsText} · 알레르기 ${groups}그룹 · 예산 ${budget.toLocaleString()}원/식`,
    periodText: `평일 ${days}일`, headcount, meals, weeks, alternatives,
    achievement, costPerPerson: 0, budgetPerPerson: budget,
    totalDays: days, totalCost: 0, sodiumCapPerDay: sodiumTarget,
    checks,
    rationale: [r.status ? `solver: ${r.status}` : '', '열량 목표 대비 산출', groups ? `대체식 ${groups}그룹 파생` : ''].filter(Boolean),
    source: 'live',
    menuRecipes: r.menu_recipes,
    menuRecipesById: r.menu_recipes_by_id,
  });
}

/* ────────────── 칸 영양 → 셀·총합 재계산 (생성 직후 + 교체·삭제 후) ──────────────
   ⚠ 로컬 재계산일 뿐 제약 재검증이 아니다(열량 밴드·3일 중복·국 궁합 등). 교체 결과를 솔버로
   다시 검증하는 '재생성'(include/exclude_menu_ids 재풀이)은 후속 과제. */
const WARN_CHECK = /^(원가|나트륨) 초과 \d+일 확인$|^검토할 경고 없음$/;

export function recomputePlan(plan: MealPlan): MealPlan {
  const perMealSodiumCap = plan.sodiumCapPerDay ? plan.sodiumCapPerDay / Math.max(1, plan.meals.length) : null;
  const known = (c: MealCell) => c.items.every((it) => it.nutri);

  // 셀: 합계 + 경고(원가가 1식 예산 초과 또는 나트륨이 1식 상한 초과면 대표 메뉴에 태그).
  const cellOf = (c: MealCell): MealCell => {
    if (!known(c)) return c; // 목업·구버전 저장본 — 계산 근거가 없으면 기존 값 유지
    let kcal = 0, protein = 0, cost = 0, sodium = 0;
    c.items.forEach(({ nutri: n }) => { kcal += n!.kcal; protein += n!.protein ?? 0; cost += n!.cost ?? 0; sodium += n!.sodium ?? 0; });
    let flag: CellFlag | undefined;
    if (cost > plan.budgetPerPerson) flag = '원가';
    else if (perMealSodiumCap && sodium > perMealSodiumCap) flag = '나트륨';
    const items = c.items.map((it) => ({ ...it, flag: undefined as CellFlag | undefined }));
    if (flag && items.length) items[Math.min(2, items.length - 1)].flag = flag;
    return { ...c, items, kcal: Math.round(kcal), protein: Math.round(protein * 10) / 10, warn: !!flag };
  };
  const mapWeeks = (ws: WeekBlock[]) => ws.map((w) => ({ ...w, days: w.days.map((d) => ({ ...d, cells: d.cells.map(cellOf) })) }));
  const weeks = mapWeeks(plan.weeks);
  const alternatives = plan.alternatives.map((t) => ({ ...t, weeks: mapWeeks(t.weeks) }));

  const allDays = weeks.flatMap((w) => w.days);
  const allCells = allDays.flatMap((d) => d.cells);
  if (!allCells.every(known)) return { ...plan, weeks, alternatives };

  // 달성률(하루 평균)·1인 원가(1식 환산)·총 식재료비 — 본식단 칸 기준.
  const days = allDays.length;
  let kcal = 0, protein = 0, sodium = 0, cost = 0;
  allCells.forEach((c) => c.items.forEach(({ nutri: n }) => {
    kcal += n!.kcal; protein += n!.protein ?? 0; sodium += n!.sodium ?? 0; cost += n!.cost ?? 0;
  }));
  const a = plan.achievement;
  const achievement: Achievement = {
    calories: { ...a.calories, value: days ? Math.round(kcal / days) : 0 },
    protein: { ...a.protein, value: days ? Math.round((protein / days) * 10) / 10 : 0 },
    sodium: {
      ...a.sodium, value: days ? Math.round(sodium / days) : 0,
      target: plan.sodiumCapPerDay ? Math.round(plan.sodiumCapPerDay) : (days ? Math.round(sodium / days) : 0),
    },
  };
  const totalPerPerson = Math.round(cost);
  const costPerPerson = days && plan.meals.length ? Math.round(totalPerPerson / (days * plan.meals.length)) : totalPerPerson;

  // 확인 항목: 경고성 항목만 다시 만들고(같은 라벨이면 완료 체크 유지), 대체식 검토 등은 그대로 둔다.
  const dayCount = (f: CellFlag) => allDays.filter((d) => d.cells.some((c) => c.items.some((it) => it.flag === f))).length;
  const doneOf = new Map(plan.checks.map((c) => [c.label, c.done]));
  const warnChecks: PlanCheck[] = [];
  const overCost = dayCount('원가'), overSod = dayCount('나트륨');
  if (overCost) warnChecks.push({ label: `원가 초과 ${overCost}일 확인`, done: false });
  if (overSod) warnChecks.push({ label: `나트륨 초과 ${overSod}일 확인`, done: false });
  const rest = plan.checks.filter((c) => !WARN_CHECK.test(c.label));
  let checks = [...warnChecks.map((c) => ({ ...c, done: doneOf.get(c.label) ?? c.done })), ...rest];
  if (!checks.length) checks = [{ label: '검토할 경고 없음', done: true }];

  return { ...plan, weeks, alternatives, achievement, costPerPerson, totalCost: totalPerPerson * plan.headcount, checks };
}

/* ────────────── 시안과 동일한 목업 데이터 ────────────── */
type Row = { m: string[]; kcal: number; protein: number; warn?: CellFlag; warnIdx?: number };

const LUNCH: Row[] = [
  { m: ['잡곡밥', '미역국', '제육볶음', '시금치나물', '배추김치'], kcal: 742, protein: 31.4 },
  { m: ['기장밥', '김치찌개', '계란말이', '콩나물무침', '깍두기'], kcal: 768, protein: 29.8, warn: '나트륨', warnIdx: 1 },
  { m: ['흑미밥', '된장국', '불고기', '숙주나물', '배추김치'], kcal: 803, protein: 34.2, warn: '원가', warnIdx: 2 },
  { m: ['보리밥', '시금치된장국', '생선까스', '단무지무침', '배추김치'], kcal: 726, protein: 30.1 },
  { m: ['잡곡밥', '유부장국', '돼지갈비찜', '두부구이', '배추김치'], kcal: 791, protein: 33.6 },
  { m: ['백미밥', '콩나물국', '닭볶음탕', '오이무침', '배추김치'], kcal: 755, protein: 32.0 },
  { m: ['기장밥', '감자국', '고등어구이', '가지볶음', '깍두기'], kcal: 738, protein: 30.8 },
  { m: ['잡곡밥', '북엇국', '돈까스', '양배추샐러드', '배추김치'], kcal: 812, protein: 31.2, warn: '나트륨', warnIdx: 1 },
  { m: ['흑미밥', '미역국', '너비아니', '숙주나물', '배추김치'], kcal: 749, protein: 32.6 },
  { m: ['보리밥', '무국', '코다리조림', '시금치나물', '깍두기'], kcal: 731, protein: 29.4 },
];
const BREAKFAST: Row[] = [
  { m: ['백미밥', '북엇국', '달걀찜'], kcal: 412, protein: 16.2 },
  { m: ['잡곡밥', '된장국', '두부부침'], kcal: 398, protein: 15.4 },
  { m: ['흑미밥', '감자국', '메추리알조림'], kcal: 431, protein: 17.0 },
  { m: ['백미밥', '미소국', '달걀말이'], kcal: 405, protein: 16.6 },
  { m: ['보리밥', '황태국', '어묵볶음'], kcal: 424, protein: 15.8, warn: '나트륨', warnIdx: 1 },
];
const DINNER: Row[] = [
  { m: ['백미밥', '육개장', '고등어조림'], kcal: 688, protein: 28.4 },
  { m: ['잡곡밥', '순두부찌개', '닭갈비'], kcal: 712, protein: 30.2 },
  { m: ['흑미밥', '설렁탕', '겉절이'], kcal: 734, protein: 31.0, warn: '원가', warnIdx: 1 },
  { m: ['보리밥', '어묵탕', '돈까스'], kcal: 726, protein: 29.6 },
  { m: ['백미밥', '김치찜', '코다리조림'], kcal: 705, protein: 28.8 },
];
const ALT_MAIN = ['두부조림', '메추리알장조림', '채소볶음', '감자조림', '어묵볶음', '연근조림'];
const DOW = ['일', '월', '화', '수', '목', '금', '토'];
const DAY_MS = 86400000;

let _mid = 0;
let _start = firstWeekday(new Date()); // 식단 1일차 날짜 — 생성 시점에 다시 잡는다
// 오늘이 평일이면 오늘, 주말이면 다음 월요일(시각은 자정으로 맞춤).
function firstWeekday(from: Date): Date {
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
  return d;
}
// i번째(0부터) 평일의 날짜 라벨·요일·주차. 주차는 1일차가 속한 주(월요일 시작)를 1주차로 센다.
function dateFor(i: number) {
  const d = new Date(_start);
  for (let n = 0; n < i;) {
    d.setDate(d.getDate() + 1);
    if (d.getDay() !== 0 && d.getDay() !== 6) n++;
  }
  const monday0 = new Date(_start);
  monday0.setDate(monday0.getDate() - (monday0.getDay() - 1));
  const week = Math.floor(Math.round((d.getTime() - monday0.getTime()) / DAY_MS) / 7);
  return { label: `${d.getMonth() + 1}/${d.getDate()}`, dow: DOW[d.getDay()], week };
}
// 식단의 첫날–마지막날 라벨(예: '9/25–10/1'). 화면·파일명 표기용.
export function planDateRange(plan: Pick<MealPlan, 'weeks'>): string {
  const days = plan.weeks.flatMap((w) => w.days);
  if (!days.length) return '';
  const first = days[0].date, last = days[days.length - 1].date;
  return first === last ? first : `${first}–${last}`;
}
// conditionText 맨 앞의 대상 라벨(예: '초등학생').
export function planTargetLabel(plan: Pick<MealPlan, 'conditionText'>): string {
  return plan.conditionText.split(' · ')[0];
}
function rowsFor(kind: MealKind): Row[] { return kind === 'breakfast' ? BREAKFAST : kind === 'dinner' ? DINNER : LUNCH; }
function toCell(kind: MealKind, i: number, alt: boolean): MealCell {
  const src = rowsFor(kind)[i % rowsFor(kind).length];
  const names = [...src.m];
  const warnIdx = alt ? undefined : src.warnIdx; // 대체식은 경고 제거
  if (alt && names.length >= 3) names[2] = ALT_MAIN[i % ALT_MAIN.length]; // 주요리 대체
  const items = names.map((name, j) => ({
    menuId: ++_mid, name,
    flag: warnIdx === j ? src.warn : undefined,
    alt: alt && j === 2,
  }));
  return { kind, items, kcal: alt ? src.kcal - 28 : src.kcal, protein: src.protein, warn: warnIdx != null };
}
function buildWeeks(meals: MealKind[], days: number, alt: boolean): WeekBlock[] {
  const blocks: WeekBlock[] = [];
  for (let i = 0; i < days; i++) {
    const { label, dow, week } = dateFor(i);
    if (!blocks[week]) blocks[week] = { label: `${week + 1}주차`, days: [] };
    blocks[week].days.push({ date: label, dow, cells: meals.map((k) => toCell(k, i, alt)) });
  }
  return blocks.filter(Boolean);
}

const PROFILE_LABEL: Record<string, string> = {
  elem_low_mix: '초등학생', elem_high_mix: '초등학생', middle_mix: '중학생', high_mix: '고등학생',
  univ_mix: '대학생', office_mix: '직장인', senior_mix: '노인', patient_general_mix: '환자',
};

// 데모용 목업 전용 시드 난수 — 입력 조건이 같으면 항상 같은 값을 내도록 결정적으로 해싱한다.
// (실제 CSP 응답이 붙으면 mockPlan()·이 함수는 통째로 제거)
function seedFrac(...nums: number[]): number {
  let h = 2166136261;
  for (const n of nums) { h ^= Math.round(n); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 10000) / 10000; // [0, 1)
}

export function mockPlan(req: MenuGenerateRequest): MealPlan {
  _mid = 0;
  _start = firstWeekday(new Date());
  const meals: MealKind[] = (req.meals?.length
    ? req.meals.map((m) => (m === '아침' ? 'breakfast' : m === '저녁' ? 'dinner' : 'lunch'))
    : ['lunch']) as MealKind[];
  const days = Math.max(1, Math.min(req.days ?? 7, 31)); // 입력 일수만큼 평일 생성
  const headcount = req.serving_count ?? 320;
  const budget = req.budget_limit_per_person ?? 4500;
  const kcalTarget = req.target_kcal_per_day ?? 1750;
  const sodiumTarget = req.sodium_max_mg_per_day ?? 1300;
  const proteinTarget = 55;

  // 데모용 목업: 입력 조건에 따라 값이 자연스럽게 소폭 달라지도록 시드 난수로 계산한다.
  // 1인 원가는 예산의 94~99% 사이에서, 예산을 절대 넘지 않는다.
  const costRatio = 0.94 + seedFrac(budget, headcount, days, kcalTarget, 1) * 0.05;
  const costPerPerson = Math.min(budget, Math.round(budget * costRatio));
  // 열량/단백질/나트륨 달성률도 목표값에 따라 조금씩 달라진다 (열량 93~102% · 단백질 100~110% · 나트륨 105~120%).
  const kcalRatio = 0.93 + seedFrac(kcalTarget, sodiumTarget, days, 2) * 0.09;
  const proteinRatio = 1.00 + seedFrac(kcalTarget, headcount, days, 3) * 0.10;
  const sodiumRatio = 1.05 + seedFrac(sodiumTarget, kcalTarget, headcount, 4) * 0.15;

  const weeks = buildWeeks(meals, days, false);
  const alternatives: AltTrack[] = (req.allergy_groups ?? []).map((g) => ({
    label: g.label, count: g.count, weeks: buildWeeks(meals, days, true),
  }));

  const profileLabel = PROFILE_LABEL[req.profile_key ?? ''] ?? (req.profile_key ?? '대상');
  const mealsText = meals.map((m) => MEAL_TABLE[m]).join('·');
  const periodText = `평일 ${days}일`;
  const groups = req.allergy_groups?.length ?? 0;

  return {
    conditionText: `${profileLabel} · ${headcount}명 · ${periodText} · ${mealsText} · 알레르기 ${groups}그룹 · 예산 ${budget.toLocaleString()}원/식`,
    periodText, headcount, meals, weeks, alternatives,
    achievement: {
      calories: { value: Math.round(kcalTarget * kcalRatio), target: kcalTarget, unit: 'kcal' },
      protein: { value: Math.round(proteinTarget * proteinRatio * 10) / 10, target: proteinTarget, unit: 'g' },
      sodium: { value: Math.round(sodiumTarget * sodiumRatio), target: sodiumTarget, unit: 'mg' },
    },
    costPerPerson, budgetPerPerson: budget, totalDays: days, totalCost: costPerPerson * headcount * days,
    checks: [
      { label: '9/16 예산 초과 확인', done: true },
      { label: '나트륨 초과 2일 확인', done: true },
      { label: "대체식 '난류·우유' 검토", done: false, view: true },
      { label: "대체식 '땅콩' 검토", done: false, view: true },
    ],
    rationale: ['열량 ±10% 충족', '동일 메뉴 3일 내 재등장 없음', '예산 이내'],
    source: 'mock',
  };
}

/* 교체 후보 (팝오버) — GET /api/menu/candidates: 같은 자리(주식/국/주찬/부찬/김치)의 실메뉴.
   예전에는 프론트 하드코딩 목록 + 가짜 수치였다. 후보는 제약 재검증 없이 제시된다(재생성은 후속). */
export interface SwapCandidate {
  menu_id: number; name: string; kcal: number; protein: number | null; sodium: number | null; cost: number;
}
export interface SwapCandidatesResponse {
  category: string; current: SwapCandidate; candidates: SwapCandidate[]; total_in_category: number;
}
export async function fetchSwapCandidates(nutritionId: number, excludeIds: number[], limit = 8): Promise<SwapCandidatesResponse> {
  const { data } = await api.get<SwapCandidatesResponse>('/api/menu/candidates', {
    params: { nutrition_id: nutritionId, exclude_ids: excludeIds.join(','), limit },
  });
  return data;
}
export const candidateNutri = (c: SwapCandidate): ItemNutri => ({ kcal: c.kcal, protein: c.protein, sodium: c.sodium, cost: c.cost });

/* 교체 가능 여부 — 그 후보로 바꿨을 때의 셀(한 끼) 원가·나트륨을 이미 정의된 상한과 비교한다.
   · 원가: 한 끼 예산(plan.budgetPerPerson). 셀 경고(recomputePlan)와 같은 기준.
   · 나트륨: 한 끼 상한 = 하루 상한 ÷ 끼니 수(recomputePlan 의 perMealSodiumCap 과 같은 기준).
     후보 나트륨을 모르면(null) 막는다 — 솔버 H-2e 의 '결측=배제' 정책과 같다.
   ⚠ 3일 반복·반상 구성 등 얽힌 제약까지의 재검증은 아니다(재생성/솔버 자리고정은 후속). */
export interface SwapCheck {
  costAfter: number; sodiumAfter: number | null;
  budget: number; sodiumCap: number | null;
  overBudget: boolean; overSodium: boolean; sodiumUnknown: boolean; allowed: boolean;
}
export function checkSwap(plan: Pick<MealPlan, 'budgetPerPerson' | 'sodiumCapPerDay' | 'meals'>, cell: MealCell,
  item: MealItem, cand: SwapCandidate): SwapCheck {
  const sum = (k: 'cost' | 'sodium') => cell.items.reduce((s, it) => s + (it.nutri?.[k] ?? 0), 0);
  const costAfter = sum('cost') - (item.nutri?.cost ?? 0) + (cand.cost ?? 0);
  const sodiumCap = plan.sodiumCapPerDay ? plan.sodiumCapPerDay / Math.max(1, plan.meals.length) : null;
  const sodiumUnknown = sodiumCap != null && cand.sodium == null;
  const sodiumAfter = cand.sodium == null ? null : sum('sodium') - (item.nutri?.sodium ?? 0) + cand.sodium;
  const overBudget = costAfter > plan.budgetPerPerson;
  const overSodium = sodiumCap != null && sodiumAfter != null && sodiumAfter > sodiumCap;
  return {
    costAfter, sodiumAfter, budget: plan.budgetPerPerson, sodiumCap,
    overBudget, overSodium, sodiumUnknown, allowed: !overBudget && !overSodium && !sodiumUnknown,
  };
}

export const PROFILE_OPTIONS = [
  { value: 'elem_low_mix', label: '초등학생', age: '만 6–11세 · 2020 한국인 영양섭취기준' },
  { value: 'middle_mix', label: '중학생', age: '만 12–14세 · 2020 한국인 영양섭취기준' },
  { value: 'high_mix', label: '고등학생', age: '만 15–17세 · 2020 한국인 영양섭취기준' },
  { value: 'senior_mix', label: '노인', age: '만 65세 이상 · 2020 한국인 영양섭취기준' },
];
export const ALLERGEN_POOL = ['난류', '우유', '땅콩', '대두', '밀', '갑각류', '고등어', '새우', '복숭아', '토마토'];
