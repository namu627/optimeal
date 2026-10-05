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
  /** 예산 방식. carryover(기본) = 기간 총액 기준(이월 허용), day = 하루 단위 상한. 백엔드 기본값도 carryover. */
  budget_mode?: BudgetMode;
  with_alternatives?: boolean;
  allergy_groups?: AllergyGroup[];
  conditions?: string[];
  exclude_menu_ids?: number[];
  include_menu_ids?: number[];
}
export interface MenuGenerateRaw { status?: string; plan?: unknown; [k: string]: unknown; }
export type BudgetMode = 'day' | 'carryover';
export const BUDGET_MODE_LABEL: Record<BudgetMode, string> = { carryover: '기간 총액 기준(이월)', day: '하루 단위' };
// 이월 모드 끼니 원가 밴드 — 백엔드 CarryoverConfig(band_low_ratio·band_high_ratio) 기본값과 같다.
export const CARRYOVER_BAND = { low: 0.8, high: 1.2 } as const;

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
  // silent: 실패는 공통 토스트 대신 생성 화면의 실패 안내(GenerateError)로 보여준다.
  const { data } = await api.post<MenuGenerateRaw>('/api/menu/generate', toWireRequest(body), { silent: true }); return data;
}

// 목업 스위치 — VITE_USE_MOCK=true 일 때만 서버를 부르지 않고 mockPlan() 을 쓴다.
// 예전에는 개발 모드에서 생성이 실패하면 조용히 목업으로 대체해, 목업을 실제 식단으로 오인하고 테스트한 일이 있었다.
// 이제 실패는 항상 실패 화면으로 보이고, 목업은 이 스위치로만 켜지며 켜져 있으면 상단 배너 + 저장·PDF·CSV 차단.
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';
export const MOCK_BANNER = '목업 데이터 — 실제 생성 결과 아님';

/** 생성 요청 실패 원인. network = 백엔드에 연결 못 함, db = 백엔드는 떴지만 영양성분 DB 연결 실패, server = 그 밖의 서버 오류. */
export type GenerateErrorKind = 'network' | 'db' | 'server';
export interface GenerateError { kind: GenerateErrorKind; detail: string }

// 백엔드가 DB 를 못 붙었을 때 내는 503 reason(menu.py _load_menu_candidates, nutrition 라우터).
const DB_REASONS = new Set(['menu_source_unavailable', 'db_unavailable']);

export function classifyGenerateError(err: unknown): GenerateError {
  const e = err as { code?: string; message?: string; response?: { status?: number; data?: { detail?: unknown } } };
  if (!e?.response) {
    if (e?.code === 'ECONNABORTED') return { kind: 'server', detail: '응답 시간 초과 — 서버가 제한 시간(90초) 안에 답하지 않았어요.' };
    return { kind: 'network', detail: e?.message ?? '네트워크 오류' };
  }
  const status = e.response.status;
  const d = e.response.data?.detail;
  const reason = d && typeof d === 'object' ? (d as { reason?: string }).reason : undefined;
  const full = d && typeof d === 'object' ? (d as { message?: string }).message : typeof d === 'string' ? d : undefined;
  const msg = full?.split('\n')[0]; // 드라이버 오류는 여러 줄 — 첫 줄만 화면에 (전문은 콘솔)
  if (status === 503 && reason && DB_REASONS.has(reason)) return { kind: 'db', detail: msg ?? reason };
  return { kind: 'server', detail: `HTTP ${status}${reason ? ` · ${reason}` : ''}${msg ? ` — ${msg}` : ''}` };
}
export function isUnavailable(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 503;
}
// 시간 안에 해를 못 찾은 응답(UNKNOWN) — 조건 충돌이 아니다(같은 조건으로 다시 풀면 풀릴 수 있음).
// 조건 충돌(INFEASIBLE) 화면과 분리해 '다시 시도·기간 줄이기·하루 단위' 안내로 보낸다.
export function isTimeoutResponse(raw: MenuGenerateRaw | null | undefined): boolean {
  return raw?.status === 'UNKNOWN';
}
// 다른 생성이 진행 중이라 서버가 거절한 응답(429 solver_busy) — 한 번에 하나씩만 푼다(백엔드 _SOLVE_LOCK).
export function isSolverBusy(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 429;
}
// 서버가 조건을 실제로 풀었으나 해가 없는 응답(INFEASIBLE 등)인지 — 목업으로 감추지 말고
// 조건 충돌 화면으로 보내야 하는 경우. status 가 OPTIMAL/FEASIBLE 이 아니거나 plan 이 비면 true.
// ⚠ UNKNOWN 도 여기서 true 이므로 호출부는 isTimeoutResponse 를 먼저 확인한다.
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
  /** 초안/확정 — plan.status 에서 뽑는다(없으면 서버가 '초안'). 구버전 서버면 undefined */
  status?: PlanStatus | null;
  /** 열량 달성률(%) — plan.achievement.calories value/target. 없으면 null */
  kcal_rate?: number | null;
}
export type PlanStatus = '초안' | '확정';
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
  /** 대체식 칸 전용 — 서버가 알레르기 대체를 못 한 메뉴(alternatives[].unresolved)의 사유. 영양사 확인 필요.
   *  안전한 메뉴로 교체하면 걷히고(origAllergyCheck 에 보관) 되돌리면 다시 붙는다(altSync.applyEdit). */
  allergyCheck?: string; origAllergyCheck?: string;
}
// cost: 이 끼니 1인 원가(칸 영양 합, recomputePlan) — 반올림 전 값. 화면에 쓸 때만 반올림한다.
// band: 이월 모드에서 기준 B×0.8~1.2 밖이면 'low'|'high'.
export interface MealCell {
  kind: MealKind; items: MealItem[]; kcal: number; protein: number; warn?: boolean;
  cost?: number; band?: 'low' | 'high';
  /** 이 끼니 1인 나트륨 합(mg, recomputePlan). 하루 합계 계산용. */
  sodium?: number;
  /** 대체식 트랙 칸 전용 — 일반식 교체·삭제가 알레르기 대체 자리에 걸려 대체식을 다시 봐야 함(pages/plan/altSync). */
  altReview?: AltReview[];
  /** 대체식 트랙 칸 전용 — 알레르기 대체가 아닌 자리라 일반식 교체·삭제를 그대로 따라갔음(대체식 표에 '일반식 교체 반영'으로 싣는다). */
  altFollow?: AltReview[];
}
/** mainMenuId: 원인이 된 일반식 메뉴 칸 id(되돌리면 이 id 로 표시를 걷는다). note: 사람이 읽는 원인. */
export interface AltReview { mainMenuId: number; note: string }
// sodium: 그날 1인 나트륨 합(mg). sodiumOver: 하루 상한(plan.sodiumCapPerDay) 초과 — 나트륨 경고는 이 날 단위로만 건다.
export interface MealDay { date: string; dow: string; cells: MealCell[]; sodium?: number; sodiumOver?: boolean }
export interface WeekBlock { label: string; days: MealDay[] }
// allergens: 이 그룹이 피해야 할 알레르겐(교체 후보에서 거르는 기준). 구버전 저장본에는 없을 수 있다.
export interface AltTrack { label: string; count: number; weeks: WeekBlock[]; allergens?: string[] }
// 박미연 KpiRow 계약: 퍼센트가 아니라 목표 대비 실제값 쌍
export interface MetricValue { value: number; target: number; unit?: string }
export interface Achievement { calories: MetricValue; protein: MetricValue; sodium: MetricValue }
export interface PlanCheck { label: string; done: boolean; view?: boolean }

// Step3 조리 지시서용 — 메뉴명 → 재료 투입량(총량)·조리순서. 백엔드가 실제로 계산해 준
// 값(recipe_ingredient_map 보강분)만 들어오며, 레시피 미보강 메뉴는 note만 채워져 온다.
// amount 는 인원수 총량. base_g(1인분)·basis(총량 기준 '스케일링'|'단순 비례')는 백엔드 _apply_scaling 이 단다 —
// 이 필드가 생기기 전 저장본에는 없고, 그때 총량은 1인분×인원(단순 비례)이었다(recipeView.amountBasis).
export interface RecipeIngredient {
  step?: number | null; name: string; amount?: number | null; unit?: string; role?: string | null;
  base_g?: number | null; basis?: '스케일링' | '단순 비례' | null;
}
// steps: 식품안전나라 원본 조리 단계(원문 그대로). 원본에 없으면 [] — 화면은 빈 상태로 둔다(임의 생성 금지).
// 이 필드가 생기기 전에 저장된 식단에는 steps 키 자체가 없다 → 레시피 화면이 다시 조회한다.
export interface MenuRecipe { cooking_method?: string | null; ingredients: RecipeIngredient[]; note?: string | null; steps?: string[] }

/** 생성 응답에 레시피가 없는 메뉴(검토에서 교체·대체식·구버전 저장본)를 조회. 투입량은 servings 명 총량. */
export async function fetchMenuRecipes(ids: number[], names: string[], servings: number): Promise<{
  by_id: Record<string, MenuRecipe>; by_name: Record<string, MenuRecipe>;
}> {
  const params = new URLSearchParams();
  ids.forEach((id) => params.append('ids', String(id)));
  names.forEach((n) => params.append('names', n));
  params.append('servings', String(servings));
  const { data } = await api.get('/api/menu/recipes', { params });
  return data;
}

/** POST /api/menu/export/pdf 본문 — 백엔드 routers/export.py ExportPdfRequest 와 동일. */
export interface PdfExportRequest {
  title: string; file_name?: string;
  summary: { label: string; value: string }[];
  /** 식단표 그리드 — 행 = 하루, 열 = 끼니. 칸 = 메뉴들 + 1인 열량·단백질. */
  grids: { title: string; corner?: string; columns: string[];
    rows: { label: string; sub?: string; cells: ({ menus: string[]; kcal?: number | null; protein?: number | null } | null)[] }[] }[];
  tables: { title: string; header: string[]; rows: (string | number | null)[][]; empty_text?: string }[];
  recipes_title?: string; recipes_note?: string;
  recipes: PdfRecipe[];
  /** 추가 조리 지시서 묶음(대체식 등) — 기본 recipes 뒤에 같은 형식으로. */
  recipe_sections?: { title: string; note?: string; recipes: PdfRecipe[] }[];
}
export interface PdfRecipe { name: string; meta?: string; ingredients: (string | number | null)[][]; steps: string[] }
/** 식단표(·조리 지시서) PDF. 한글 폰트는 백엔드가 임베드한다. */
export async function exportPlanPdf(body: PdfExportRequest): Promise<Blob> {
  const { data } = await api.post<Blob>('/api/menu/export/pdf', body, { responseType: 'blob' });
  return data;
}

export interface MealPlan {
  conditionText: string; periodText: string; headcount: number;
  meals: MealKind[]; weeks: WeekBlock[]; alternatives: AltTrack[];
  achievement: Achievement; costPerPerson: number; budgetPerPerson: number;
  totalDays: number; totalCost: number;
  /** 1일 나트륨 상한(mg, 백엔드 적용값). 셀 나트륨 경고(상한÷끼니 수) 재계산용. 없으면 경고 안 함. */
  sodiumCapPerDay?: number | null;
  checks: PlanCheck[]; rationale: string[]; source: 'live' | 'mock';
  /** 저장 상태. 확정 화면에서 '확정'으로 저장한다. 없으면 초안으로 본다. */
  status?: PlanStatus;
  /** 예산 방식(백엔드 적용값). 이 필드가 생기기 전 저장된 식단에는 없다 → 'day' 로 취급(budgetModeOf). */
  budgetMode?: BudgetMode;
  /** 1인 기간 총예산(원) = 1끼 예산 × 끼니 수 × 일수. carryover 에서 Hard 상한. */
  budgetTotal?: number | null;
  /** 생성 시점 서버 이월 리포트(끼니별 원가·통계). 교체 후 수치는 budgetSummary 가 칸 영양으로 다시 계산한다. */
  carryover?: CarryoverReport | null;
  /** 끼니 원가 울타리(원, carryover Hard). 없거나 풀렸으면 null. */
  guardMin?: number | null;
  guardMax?: number | null;
  /** 울타리로는 해가 없어서 울타리 없이 다시 생성했는지(stop_reason='guard_relaxed'). */
  guardRelaxed?: boolean;
  menuRecipes?: Record<string, MenuRecipe>;
  /** nutrition_id(문자열 키) → 레시피. 있으면 MealItem.nutritionId 로 먼저 찾는다(동명 메뉴 정확). */
  menuRecipesById?: Record<string, MenuRecipe>;
  /** 조리 인원(문자열 키) → nutrition_id → 그 인원 총량 레시피. 전체 인원 레시피는 menuRecipesById. 없으면 서버 조회. */
  menuRecipesByServings?: Record<string, Record<string, MenuRecipe>>;
}

/* ────────────── 실제 /api/menu/generate 응답 → 뷰모델 매핑 ──────────────
   plan 은 {일: {끼니(한글): [메뉴명,...]}}, 열량은 daily_kcal(하루 총합)만 온다.
   plan_ids 는 같은 모양의 {일: {끼니: [nutrition_id,...]}}(솔버가 고른 행). 있으면 메뉴별
   열량·단백질·나트륨·원가를 menu_nutrition_by_id 에서, 없으면(구버전 백엔드) menu_nutrition(이름)에서 채운다. */
// cost 는 원 단위 반올림값(표시·구버전 호환), cost_exact 는 반올림 전 값(합계용). 구버전 백엔드에는 cost_exact 가 없다.
interface MenuNutri { kcal?: number; protein?: number | null; sodium?: number | null; cost?: number | null; cost_exact?: number | null }
type PlanIds = Record<string, Record<string, (number | null)[]>>; // 대체식은 못 찾은 칸이 null
// 백엔드 응답 carryover(module_3 evaluate_carryover_breakdown) 중 화면이 쓰는 부분.
export interface CarryoverReport {
  meal_costs: { day: number; meal_index: number; cost: number }[];
  per_meal_budget: number; max_meal_cost: number; min_meal_cost: number;
  meals_over_budget: number; meals_above_band: number; meals_below_band: number;
  max_consecutive_cost_diff: number; max_consecutive_sodium_diff?: number;
  total_cost: number; total_budget: number | null; total_headroom: number | null;
}
interface GenerateResponse {
  status?: string;
  stop_reason?: string | null;
  applied_targets?: {
    meals?: string[]; target_kcal_per_day?: number; sodium_max_mg_per_day?: number | null;
    protein_g?: number | null; // 프로파일 기준 단백질 목표(끼니 수 반영). 프로파일 미지정 시 null
    budget_mode?: BudgetMode; budget_total?: number | null; budget_per_meal?: number | null;
    guard_min_won?: number | null; guard_max_won?: number | null;
  };
  carryover?: CarryoverReport | null;
  plan?: Record<string, Record<string, string[]>>;
  daily_kcal?: Record<string, number>;
  total_cost_won?: number;
  menu_nutrition?: Record<string, MenuNutri>;
  menu_recipes?: Record<string, MenuRecipe>;
  plan_ids?: PlanIds | null;
  menu_nutrition_by_id?: Record<string, MenuNutri & { name?: string }>;
  menu_recipes_by_id?: Record<string, MenuRecipe>;
  /** 전체 인원이 아닌 조리 인원별 레시피 {인원: {menu_id: 레시피}} — 대체식 그룹·대체 인원을 뺀 일반식(backend _recipes_by_servings). */
  menu_recipes_by_servings?: Record<string, Record<string, MenuRecipe>> | null;
  hard_breakdown?: HardBreakdown | null;
  alternatives?: Array<{
    group?: { label?: string; allergens?: string[]; count?: number };
    plan?: Record<string, Record<string, string[]>>;
    /** plan 과 같은 모양의 메뉴 id(백엔드가 본식단 id·대표행으로 붙임). 구버전 백엔드는 없음 */
    plan_ids?: PlanIds | null;
    /** 대체를 못 한 접시(module_3 Substitution) — reason: '안전한 대체 메뉴 없음' | '재료 정보 없음 — …' */
    unresolved?: Array<{ day: number; meal: string; original: string; hit_allergens?: string[]; reason?: string }>;
  }>;
}
// module_3 evaluate_hard_breakdown 의 리포트(풀린 해를 실측한 제약 충족 여부). 미적용 항목은 null·빈 값.
interface HardBreakdown {
  per_day?: Array<{ day: number; kcal: number; kcal_ok: boolean | null; cost: number; budget_ok: boolean | null }>;
  budget_total?: { total: number; limit: number; headroom: number; ok: boolean } | null;
  kcal_bounds?: [number, number] | null;
  excluded_menu_count?: number;
  excluded_clean?: boolean;
  menu_repeat?: { window_days: number; violations: unknown[] };
  nutrient_max?: Record<string, { limit: number; max_day: number; all_ok: boolean }>;
  pairing?: { all_ok?: boolean };
  manual?: { all_ok?: boolean };
}

/* 생성 근거(검토 화면 칩) — hard_breakdown 에서 **실제로 충족된** 제약만 문장으로 만든다.
   미적용(null)·위반 항목은 넣지 않는다(위반은 셀 경고·확인 필요 목록이 다룬다).
   예산: day 는 하루 상한(한 끼 × 끼니 수)으로 검사하므로 끼니가 여럿이면 1일 값으로 적는다.
   carryover 는 기간 총액(budget_total)을 검사하므로 총예산과 실제 끼니 평균을 적는다. */
function buildRationale(hb: HardBreakdown, kcalTarget: number, budgetPerMeal: number, nMeals: number,
  mode: BudgetMode = 'day'): string[] {
  const out: string[] = [];
  const days = hb.per_day ?? [];
  const allTrue = (vals: (boolean | null)[]) => vals.length > 0 && vals.every((v) => v === true);
  if (allTrue(days.map((d) => d.kcal_ok))) {
    const b = hb.kcal_bounds;
    out.push(`열량 ${Math.round(kcalTarget).toLocaleString()}kcal 목표 충족${b ? ` (${Math.round(b[0]).toLocaleString()}~${Math.round(b[1]).toLocaleString()})` : ''}`);
  }
  const bt = hb.budget_total;
  if (mode === 'carryover' && bt?.ok) {
    const avg = days.length && nMeals ? Math.round(bt.total / (days.length * nMeals)) : 0;
    out.push(`기간 총예산 ${bt.limit.toLocaleString()}원 이내 · 끼니 평균 ${avg.toLocaleString()}원`);
  } else if (allTrue(days.map((d) => d.budget_ok))) {
    out.push(nMeals > 1
      ? `예산 1일 ${(budgetPerMeal * nMeals).toLocaleString()}원 이내 (${budgetPerMeal.toLocaleString()}원/식 × ${nMeals}끼)`
      : `예산 ${budgetPerMeal.toLocaleString()}원/식 이내`);
  }
  const sodium = hb.nutrient_max?.sodium;
  if (sodium?.all_ok) out.push(`나트륨 ${Math.round(sodium.limit).toLocaleString()}mg/일 이하`);
  const rep = hb.menu_repeat;
  if (rep && rep.window_days > 0 && rep.violations.length === 0) {
    out.push(rep.window_days > 1 ? `${rep.window_days}일 내 동일 메뉴 없음` : '같은 날 동일 메뉴 없음');
  }
  if ((hb.excluded_menu_count ?? 0) > 0 && hb.excluded_clean) out.push(`배제 메뉴 ${hb.excluded_menu_count}종 미편성`);
  if (hb.pairing?.all_ok) out.push('주식·국 궁합 적합');
  if (hb.manual?.all_ok) out.push('수동 지정 메뉴 반영');
  return out;
}

const MEAL_FROM_KR: Record<string, MealKind> = { 아침: 'breakfast', 점심: 'lunch', 저녁: 'dinner' };
const MEAL_ORDER = ['아침', '점심', '저녁'];

/** 알레르기 대체를 못 한 접시 → {일: {끼니: {메뉴명: 사유}}}. 사유 = '난류 · 안전한 대체 메뉴 없음' 처럼 걸린 알레르겐 + 서버 사유. */
export function unresolvedChecks(list?: NonNullable<GenerateResponse['alternatives']>[number]['unresolved']) {
  const out: Record<string, Record<string, Record<string, string>>> = {};
  (list ?? []).forEach((u) => {
    const why = [(u.hit_allergens ?? []).join('·'), u.reason || '안전한 대체 메뉴 없음'].filter(Boolean).join(' · ');
    ((out[String(u.day)] ??= {})[u.meal] ??= {})[u.original] = why;
  });
  return out;
}

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
  const budget = r.applied_targets?.budget_per_meal ?? req.budget_limit_per_person ?? 4500;
  const headcount = req.serving_count ?? 320;
  // 백엔드가 알려준 적용 모드. 구버전 백엔드(필드 없음)는 기존 하루 단위로 동작했으므로 'day'.
  const budgetMode: BudgetMode = r.applied_targets?.budget_mode ?? 'day';

  // 응답에 등장하는 끼니를 표준 순서(아침→점심→저녁)로 정렬해 MealKind 로 변환.
  const mealKr = (r.applied_targets?.meals?.length ? r.applied_targets.meals
    : Array.from(new Set(Object.values(plan).flatMap((d) => Object.keys(d)))));
  const meals: MealKind[] = MEAL_ORDER.filter((m) => mealKr.includes(m)).map((m) => MEAL_FROM_KR[m]);

  const sodiumTarget = r.applied_targets?.sodium_max_mg_per_day ?? req.sodium_max_mg_per_day ?? null;

  // 칸마다 1인분 영양·원가(nutri)만 싣는다. 셀 합계·경고·달성률·원가는 recomputePlan 이 계산한다
  // (생성 직후와 교체·삭제 후가 같은 계산을 쓰도록).
  // baseNames: 대체식 칸일 때 본식단 같은 자리의 메뉴명 — 실제로 바뀐 메뉴에만 '대체' 배지를 단다.
  // checks: 대체식 칸에서 알레르기 대체를 못 한 메뉴명 → 사유(allergyCheck).
  const buildCell = (kind: MealKind, names: string[], alt: boolean, ids?: (number | null)[], baseNames?: string[],
    checks?: Record<string, string>): MealCell => {
    const items: MealItem[] = names.map((name, i) => {
      const nutritionId = ids?.[i] ?? undefined; // plan_ids 는 plan 과 같은 위치
      const n = nutriOf(name, nutritionId);
      const itemNutri: ItemNutri = { kcal: n.kcal ?? 0, protein: n.protein ?? null, sodium: n.sodium ?? null, cost: n.cost_exact ?? n.cost ?? null };
      const changed = alt && baseNames?.[i] !== name;
      return { menuId: ++_mid, nutritionId, name, alt: changed || undefined, nutri: itemNutri,
        ...(checks?.[name] ? { allergyCheck: checks[name] } : {}) };
    });
    return { kind, items, kcal: 0, protein: 0 };
  };

  // idsObj: 본식단은 plan_ids, 대체식은 alternatives[].plan_ids(백엔드가 붙임 — 없으면 교체 불가 칸).
  const weeksFrom = (planObj: Record<string, Record<string, string[]>>, alt: boolean, idsObj?: PlanIds | null,
    checks?: Record<string, Record<string, Record<string, string>>>): WeekBlock[] => {
    const keys = Object.keys(planObj).sort((a, b) => Number(a) - Number(b));
    const blocks: WeekBlock[] = [];
    keys.forEach((dayKey, i) => {
      const { label, dow, week } = dateFor(i);
      if (!blocks[week]) blocks[week] = { label: `${week + 1}주차`, days: [] };
      const dayPlan = planObj[dayKey] ?? {};
      const cells = meals.map((k) => {
        const kr = MEAL_ORDER.find((o) => MEAL_FROM_KR[o] === k)!;
        return buildCell(k, dayPlan[kr] ?? [], alt, idsObj?.[dayKey]?.[kr], alt ? plan[dayKey]?.[kr] : undefined,
          checks?.[dayKey]?.[kr]);
      });
      blocks[week].days.push({ date: label, dow, cells });
    });
    return blocks.filter(Boolean);
  };

  _mid = 0;
  _start = firstWeekday(new Date());
  const weeks = weeksFrom(plan, false, planIds);
  const alternatives: AltTrack[] = (r.alternatives ?? []).map((a) => ({
    label: a.group?.label ?? '대체식', count: a.group?.count ?? 0, allergens: a.group?.allergens ?? [],
    weeks: weeksFrom(a.plan ?? {}, true, a.plan_ids, unresolvedChecks(a.unresolved)),
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
    conditionText: `${profileLabel} · ${headcount}명 · 평일 ${days}일 · ${mealsText} · 알레르기 ${groups}그룹 · 예산 ${budget.toLocaleString()}원/식`
      + (budgetMode === 'carryover' ? ' (기간 총액 기준)' : ''),
    periodText: `평일 ${days}일`, headcount, meals, weeks, alternatives,
    achievement, costPerPerson: 0, budgetPerPerson: budget,
    totalDays: days, totalCost: 0, sodiumCapPerDay: sodiumTarget,
    budgetMode, budgetTotal: r.applied_targets?.budget_total ?? null, carryover: r.carryover ?? null,
    guardMin: r.applied_targets?.guard_min_won ?? null, guardMax: r.applied_targets?.guard_max_won ?? null,
    guardRelaxed: r.stop_reason === 'guard_relaxed',
    checks,
    // 구버전 백엔드(hard_breakdown 없음)만 예전 일반 문구로 폴백.
    rationale: r.hard_breakdown
      ? buildRationale(r.hard_breakdown, kcalTarget, budget, meals.length, budgetMode)
      : [r.status ? `solver: ${r.status}` : '', '열량 목표 대비 산출', groups ? `대체식 ${groups}그룹 파생` : ''].filter(Boolean),
    source: 'live',
    menuRecipes: r.menu_recipes,
    menuRecipesById: r.menu_recipes_by_id,
    menuRecipesByServings: r.menu_recipes_by_servings ?? undefined,
  });
}

/* ────────────── 칸 영양 → 셀·총합 재계산 (생성 직후 + 교체·삭제 후) ──────────────
   ⚠ 로컬 재계산일 뿐 제약 재검증이 아니다(열량 밴드·3일 중복·국 궁합 등). 교체 결과를 솔버로
   다시 검증하는 '재생성'(include/exclude_menu_ids 재풀이)은 후속 과제. */
// 예전 라벨('나트륨 초과 N일 확인')도 지워지도록 둘 다 맞춘다(구버전 저장본을 다시 계산할 때).
const WARN_CHECK = /^(원가|나트륨|나트륨 하루 상한) 초과 \d+일 확인$|^검토할 경고 없음$|^기간 총예산 초과 확인$/;

/** 저장본 호환: budgetMode 가 없던 시절 식단은 하루 단위로 만들어졌다. */
export const budgetModeOf = (plan: Pick<MealPlan, 'budgetMode'>): BudgetMode => plan.budgetMode ?? 'day';

/** 끼니 원가가 이월 밴드(B×0.8~1.2) 밖인지. */
export function bandOf(cost: number, perMeal: number): 'low' | 'high' | undefined {
  if (cost > perMeal * CARRYOVER_BAND.high) return 'high';
  if (cost < perMeal * CARRYOVER_BAND.low) return 'low';
  return undefined;
}

export function recomputePlan(plan: MealPlan): MealPlan {
  const sodiumCap = plan.sodiumCapPerDay ?? null;
  const known = (c: MealCell) => c.items.every((it) => it.nutri);
  const carryover = budgetModeOf(plan) === 'carryover';

  // 셀: 합계 + 원가 경고. day: 원가가 1식 예산 초과면 '원가' 태그. carryover: 한 끼가 B 를 넘는 건 허용(이월)이라
  //   '원가' 경고 대신 밴드(B×0.8~1.2) 밖이면 band 로 참고 표시만 한다.
  //   나트륨은 칸 단위로 판정하지 않는다(하루 상한÷끼니 수 기준은 하루 상한을 지켜도 절반 가까운 칸이 경고였다 — 2026-09-30).
  const cellOf = (c: MealCell): MealCell => {
    if (!known(c)) return c; // 목업·구버전 저장본 — 계산 근거가 없으면 기존 값 유지
    let kcal = 0, protein = 0, cost = 0, sodium = 0;
    c.items.forEach(({ nutri: n }) => { kcal += n!.kcal; protein += n!.protein ?? 0; cost += n!.cost ?? 0; sodium += n!.sodium ?? 0; });
    const flag: CellFlag | undefined = !carryover && cost > plan.budgetPerPerson ? '원가' : undefined;
    const items = c.items.map((it) => ({ ...it, flag: undefined as CellFlag | undefined }));
    if (flag && items.length) items[Math.min(2, items.length - 1)].flag = flag;
    return {
      ...c, items, kcal: Math.round(kcal), protein: Math.round(protein * 10) / 10, warn: !!flag,
      cost, sodium, band: carryover ? bandOf(cost, plan.budgetPerPerson) : undefined,
    };
  };
  // 날: 하루 나트륨 합계 > 하루 상한인 날만 경고 — 그날 칸 전부 warn + 나트륨이 가장 높은 메뉴에 '나트륨' 태그.
  //   가장 높은 메뉴 하나를 빼도 여전히 상한을 넘으면 두 번째 메뉴까지(최대 2개) 태그한다.
  const dayOf = (d: MealDay): MealDay => {
    const cells = d.cells.map(cellOf);
    if (sodiumCap == null || !d.cells.every(known)) return { ...d, cells, sodium: undefined, sodiumOver: false };
    const daySodium = cells.reduce((s, c) => s + (c.sodium ?? 0), 0);
    if (daySodium <= sodiumCap) return { ...d, cells, sodium: daySodium, sodiumOver: false };
    const ranked = cells.flatMap((c, ci) => c.items.map((it, ii) => ({ ci, ii, na: it.nutri?.sodium ?? 0 })))
      .sort((a, b) => b.na - a.na);
    const tagged = ranked.slice(0, ranked.length > 1 && daySodium - ranked[0].na > sodiumCap ? 2 : 1);
    return {
      ...d, sodium: daySodium, sodiumOver: true,
      cells: cells.map((c, ci) => ({
        ...c, warn: true,
        items: c.items.map((it, ii) => (!it.flag && tagged.some((t) => t.ci === ci && t.ii === ii) ? { ...it, flag: '나트륨' as CellFlag } : it)),
      })),
    };
  };
  const mapWeeks = (ws: WeekBlock[]) => ws.map((w) => ({ ...w, days: w.days.map(dayOf) }));
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
  // 나트륨: 하루 합계가 상한을 실제로 넘은 날 수. 0일이면 항목을 만들지 않는다.
  const overCost = dayCount('원가'), overSod = allDays.filter((d) => d.sodiumOver).length;
  if (overCost) warnChecks.push({ label: `원가 초과 ${overCost}일 확인`, done: false });
  if (overSod) warnChecks.push({ label: `나트륨 하루 상한 초과 ${overSod}일 확인`, done: false });
  if (carryover && plan.budgetTotal != null && totalPerPerson > plan.budgetTotal) {
    warnChecks.push({ label: '기간 총예산 초과 확인', done: false });
  }
  const rest = plan.checks.filter((c) => !WARN_CHECK.test(c.label));
  let checks = [...warnChecks.map((c) => ({ ...c, done: doneOf.get(c.label) ?? c.done })), ...rest];
  if (!checks.length) checks = [{ label: '검토할 경고 없음', done: true }];

  return { ...plan, weeks, alternatives, achievement, costPerPerson, totalCost: Math.round(planTotalCost({ ...plan, weeks, alternatives })), checks };
}

/** 총 식재료비(원, 반올림 전) — 끼니마다 알레르기 그룹은 자기 대체식 칸, 나머지 인원은 일반식 칸 원가.
 *  예전엔 일반식 1인 원가 × 전체 인원이라 대체 메뉴 원가 차이가 빠졌다. 그룹 대체식 칸이 없는 끼니는 일반식을 먹는다.
 *  (조리 지시서 인원 규칙 recipeView.mainServingsOf 와 같은 기준 — 대체식 칸에는 같이 먹는 메뉴도 들어 있다.) */
export function planTotalCost(plan: Pick<MealPlan, 'weeks' | 'alternatives' | 'headcount'>): number {
  const cellCost = (c?: MealCell) => (c?.items ?? []).reduce((s, it) => s + (it.nutri?.cost ?? 0), 0);
  let total = 0;
  plan.weeks.forEach((wk, w) => wk.days.forEach((d, di) => d.cells.forEach((c) => {
    let mainEaters = plan.headcount;
    plan.alternatives.forEach((t) => {
      const alt = t.weeks[w]?.days[di]?.cells.find((x) => x.kind === c.kind);
      if (!alt || t.count < 1) return;
      total += cellCost(alt) * t.count;
      mainEaters -= t.count;
    });
    total += cellCost(c) * Math.max(0, mainEaters);
  })));
  return total;
}

/** 본식단 1인 기간 총원가(칸 영양 합, 반올림 전). 교체 판정·총액 요약이 같은 값을 쓰고 표시할 때만 반올림한다. */
export function planPeriodCost(plan: Pick<MealPlan, 'weeks'>): number {
  return plan.weeks.flatMap((w) => w.days.flatMap((d) => d.cells))
    .reduce((s, c) => s + c.items.reduce((t, it) => t + (it.nutri?.cost ?? 0), 0), 0);
}

/** 이월 모드 총액 요약(교체 후에도 칸 영양으로 즉시 다시 계산). carryover 가 아니거나 총예산을 모르면 null. */
export interface BudgetSummary {
  total: number; budget: number; headroom: number; mealAvg: number; outOfBand: number; meals: number;
  guardMin: number | null; guardMax: number | null; guardRelaxed: boolean;
}
export function budgetSummary(plan: MealPlan): BudgetSummary | null {
  if (budgetModeOf(plan) !== 'carryover' || plan.budgetTotal == null) return null;
  const cells = plan.weeks.flatMap((w) => w.days.flatMap((d) => d.cells));
  const total = planPeriodCost(plan);
  return {
    total: Math.round(total), budget: Math.round(plan.budgetTotal), headroom: Math.round(plan.budgetTotal - total),
    mealAvg: cells.length ? Math.round(total / cells.length) : 0,
    outOfBand: cells.filter((c) => c.band).length, meals: cells.length,
    guardMin: plan.guardMin != null ? Math.round(plan.guardMin) : null,
    guardMax: plan.guardMax != null ? Math.round(plan.guardMax) : null,
    guardRelaxed: !!plan.guardRelaxed,
  };
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

// 대상 프로파일 선택지 — 백엔드 영양 기준 표(data/processed/user_group_profiles.csv)의 혼성(_mix) 프로파일과 1:1.
// label 은 화면 선택지·조건 요약(conditionText 맨 앞)·식단 목록 대상 필터에 그대로 쓰이므로 선택지끼리 겹치면 안 된다.
// age 는 GET /api/menu/profiles 조회가 실패했을 때만 보이는 대체 문구다(성공하면 백엔드 값이 표시된다).
export const PROFILE_OPTIONS = [
  { value: 'elem_low_mix', label: '초등 저학년', age: '만 6–8세(1~3학년) · 2025 한국인 영양소 섭취기준' },
  { value: 'elem_high_mix', label: '초등 고학년', age: '만 9–11세(4~6학년) · 2025 한국인 영양소 섭취기준' },
  { value: 'middle_mix', label: '중학생', age: '만 12–14세 · 2025 한국인 영양소 섭취기준' },
  { value: 'high_mix', label: '고등학생', age: '만 15–18세 · 2025 한국인 영양소 섭취기준' },
  { value: 'univ_mix', label: '성인(19–29세)', age: '만 19–29세 · 2025 한국인 영양소 섭취기준' },
  { value: 'office_mix', label: '성인(30–49세)', age: '만 30–49세 · 2025 한국인 영양소 섭취기준' },
  { value: 'senior_mix', label: '노인(65–74세)', age: '만 65–74세 · 2025 한국인 영양소 섭취기준' },
  { value: 'senior75_mix', label: '노인(75세 이상)', age: '만 75세 이상 · 2025 한국인 영양소 섭취기준' },
];
// profile_key → 화면 라벨. 선택지에 없는 키(환자 일반식)는 백엔드에만 있는 프로파일이다.
const PROFILE_LABEL: Record<string, string> = {
  ...Object.fromEntries(PROFILE_OPTIONS.map((p) => [p.value, p.label])),
  patient_general_mix: '환자',
};
// 선택지를 백엔드 기준으로 나누기 전(2026-10-04 이전)에 저장된 식단의 대상 라벨 → profile_key.
const LEGACY_PROFILE_LABEL: Record<string, string> = {
  초등학생: 'elem_low_mix', 노인: 'senior_mix', 대학생: 'univ_mix', 직장인: 'office_mix', 성인: 'office_mix',
};
/** 대상 라벨(조건 요약 맨 앞) → profile_key. 예전 저장본의 라벨도 받는다. 모르는 라벨이면 undefined. */
export function profileKeyOfLabel(label: string): string | undefined {
  return PROFILE_OPTIONS.find((p) => p.label === label)?.value ?? LEGACY_PROFILE_LABEL[label];
}

// 데모용 목업 전용 시드 난수 — 입력 조건이 같으면 항상 같은 값을 내도록 결정적으로 해싱한다.
// (VITE_USE_MOCK=true 일 때만 쓰인다 — USE_MOCK 참고)
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
    label: g.label, count: g.count, allergens: g.allergens, weeks: buildWeeks(meals, days, true),
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
  /** carryover 판정(plan_total_cost·total_budget 를 넘겼을 때만): 교체 후 기간 총액·총예산 이내 여부. */
  period_total_after?: number; within_total_budget?: boolean;
  /** carryover 울타리 판정(meal_cost·guard_* 를 넘겼을 때만): 교체 후 그 끼니 원가·울타리 안 여부. */
  meal_cost_after?: number; within_meal_guard?: boolean;
  /** 반올림 전 원가(합계용). 구버전 백엔드에는 없다. */
  cost_exact?: number;
}
export interface SwapCandidatesResponse {
  category: string; current: SwapCandidate; candidates: SwapCandidate[]; total_in_category: number;
}
export interface TotalBudgetQuery {
  planTotalCost: number; totalBudget: number;
  /** 울타리 판정용: 교체하려는 끼니의 현재 원가와 울타리(원). 울타리가 없으면 생략. */
  mealCost?: number; guardMin?: number | null; guardMax?: number | null;
}
// total: carryover 모드면 교체 후 기간 총액·끼니 원가 울타리 판정용 값.
// allergens: 대체식 칸이면 그 그룹의 알레르겐 — 서버가 교차반응까지 넓혀 후보에서 뺀다.
export async function fetchSwapCandidates(nutritionId: number, excludeIds: number[], limit = 8,
  total?: TotalBudgetQuery | null, allergens: string[] = []): Promise<SwapCandidatesResponse> {
  const { data } = await api.get<SwapCandidatesResponse>('/api/menu/candidates', {
    params: {
      nutrition_id: nutritionId, exclude_ids: excludeIds.join(','), limit, exclude_allergens: allergens.join(','),
      ...(total ? { plan_total_cost: total.planTotalCost, total_budget: total.totalBudget } : {}),
      ...(total && total.mealCost != null && (total.guardMin != null || total.guardMax != null)
        ? { meal_cost: total.mealCost, ...(total.guardMin != null ? { guard_min: total.guardMin } : {}),
            ...(total.guardMax != null ? { guard_max: total.guardMax } : {}) }
        : {}),
    },
  });
  return data;
}
export const candidateNutri = (c: SwapCandidate): ItemNutri => ({ kcal: c.kcal, protein: c.protein, sodium: c.sodium, cost: c.cost_exact ?? c.cost });

/* 교체 가능 여부 — 그 후보로 바꿨을 때의 셀(한 끼) 원가·나트륨을 이미 정의된 상한과 비교한다.
   · 원가(day): 한 끼 예산(plan.budgetPerPerson). 셀 경고(recomputePlan)와 같은 기준.
   · 원가(carryover): 교체 후 기간 총액 ≤ 총예산(plan.budgetTotal). 서버 판정(within_total_budget)을 쓰고,
     없으면(구버전 백엔드) 같은 식으로 로컬 계산. 한 끼가 B 를 넘는 것은 막지 않는다(이월).
   · 끼니 울타리(carryover): 교체 후 그 끼니 원가가 울타리(guardMin~guardMax) 밖이면 막는다.
     서버 판정(within_meal_guard) 우선, 없으면 로컬 계산. 총예산·울타리 사유는 둘 다 따로 보고한다.
   · 나트륨: 교체 후 **그날 하루 합계**가 하루 상한을 넘는지 알려 준다(overSodium). 막지는 않는다 —
     경고는 recomputePlan 이 그날에 건다(칸 단위 기준은 2026-09-30 폐기).
     후보 나트륨을 모르면(null) 하루 합계를 계산할 수 없어 막는다 — 솔버 H-2e 의 '결측=배제' 정책과 같다.
   ⚠ 3일 반복·반상 구성 등 얽힌 제약까지의 재검증은 아니다(재생성/솔버 자리고정은 후속). */
export interface SwapCheck {
  /** sodiumAfter: 교체 후 그날 하루 나트륨 합(mg). sodiumCap: 하루 상한. */
  costAfter: number; sodiumAfter: number | null;
  budget: number; sodiumCap: number | null;
  overBudget: boolean; overSodium: boolean; sodiumUnknown: boolean; allowed: boolean;
  mode: BudgetMode;
  /** carryover 에서만: 교체 후 1인 기간 총액과 총예산. */
  periodAfter?: number; totalBudget?: number;
  /** carryover 울타리: 교체 후 끼니 원가가 울타리 밖인지와 그 울타리(원). */
  overGuard: boolean; guardMin?: number | null; guardMax?: number | null;
}
export function checkSwap(plan: Pick<MealPlan, 'budgetPerPerson' | 'sodiumCapPerDay' | 'meals' | 'budgetMode' | 'budgetTotal' | 'weeks' | 'guardMin' | 'guardMax'>,
  cell: MealCell, item: MealItem, cand: SwapCandidate): SwapCheck {
  const sum = (k: 'cost' | 'sodium') => cell.items.reduce((s, it) => s + (it.nutri?.[k] ?? 0), 0);
  const candCost = cand.cost_exact ?? cand.cost ?? 0;
  const costAfter = sum('cost') - (item.nutri?.cost ?? 0) + candCost;
  const sodiumCap = plan.sodiumCapPerDay ?? null;
  const sodiumUnknown = sodiumCap != null && cand.sodium == null;
  // 이 칸이 속한 날의 하루 나트륨 합(교체 전) — 칸 식별자(menuId)로 그날을 찾는다.
  const day = plan.weeks.flatMap((w) => w.days).find((d) => d.cells.some((c) => c.items.some((it) => it.menuId === item.menuId)));
  const daySodium = day ? day.cells.reduce((s, c) => s + c.items.reduce((t, it) => t + (it.nutri?.sodium ?? 0), 0), 0) : sum('sodium');
  const sodiumAfter = cand.sodium == null ? null : daySodium - (item.nutri?.sodium ?? 0) + cand.sodium;
  const mode = budgetModeOf(plan);
  let overBudget: boolean, overGuard = false;
  let periodAfter: number | undefined, totalBudget: number | undefined;
  const guardMin = plan.guardMin ?? null, guardMax = plan.guardMax ?? null;
  if (mode === 'carryover' && plan.budgetTotal != null) {
    totalBudget = plan.budgetTotal;
    periodAfter = planPeriodCost(plan) - (item.nutri?.cost ?? 0) + candCost;
    overBudget = cand.within_total_budget != null ? !cand.within_total_budget : periodAfter > totalBudget;
    if (guardMin != null || guardMax != null) {
      overGuard = cand.within_meal_guard != null ? !cand.within_meal_guard
        : (guardMin != null && costAfter < guardMin) || (guardMax != null && costAfter > guardMax);
    }
  } else {
    overBudget = costAfter > plan.budgetPerPerson;
  }
  const overSodium = sodiumCap != null && sodiumAfter != null && sodiumAfter > sodiumCap;
  return {
    costAfter, sodiumAfter, budget: plan.budgetPerPerson, sodiumCap,
    overBudget, overSodium, sodiumUnknown, allowed: !overBudget && !overGuard && !sodiumUnknown,
    mode, periodAfter, totalBudget, overGuard, guardMin, guardMax,
  };
}

// 알레르기 표시 대상 19종(NEIS 급식 알레르기 코드 1~19 순). 서버 메뉴 알레르겐이 이 이름들이다
// (scripts/load_allergen_constraints.py). 구버전 저장본의 '갑각류'는 서버가 게·새우로 펼친다.
export const ALLERGEN_POOL = [
  '난류', '우유', '메밀', '땅콩', '대두', '밀', '고등어', '게', '새우', '돼지고기',
  '복숭아', '토마토', '아황산류', '호두', '닭고기', '쇠고기', '오징어', '조개류', '잣',
];