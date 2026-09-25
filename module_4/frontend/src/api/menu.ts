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
export async function generateMenu(body: MenuGenerateRequest): Promise<MenuGenerateRaw> {
  const { data } = await api.post<MenuGenerateRaw>('/api/menu/generate', body); return data;
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

/* ────────────── 뷰모델 ────────────── */
export type MealKind = 'breakfast' | 'lunch' | 'dinner';
export const MEAL_KR: Record<MealKind, string> = { breakfast: '아침', lunch: '점심', dinner: '저녁' };
export const MEAL_TABLE: Record<MealKind, string> = { breakfast: '조식', lunch: '중식', dinner: '석식' };
export const MEAL_TIME: Record<MealKind, string> = { breakfast: '08:00', lunch: '12:20', dinner: '17:40' };

export type CellFlag = '나트륨' | '원가';
export interface MealItem { menuId?: number; name: string; flag?: CellFlag; alt?: boolean; orig?: string }
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
  checks: PlanCheck[]; rationale: string[]; source: 'live' | 'mock';
  menuRecipes?: Record<string, MenuRecipe>;
}

/* ────────────── 실제 /api/menu/generate 응답 → 뷰모델 매핑 ──────────────
   plan 은 {일: {끼니(한글): [메뉴명,...]}}, 열량은 daily_kcal(하루 총합)만 온다.
   메뉴별 열량·단백질·나트륨·원가는 menu_nutrition({메뉴명: {...}})으로 채운다. */
interface MenuNutri { kcal?: number; protein?: number | null; sodium?: number | null; cost?: number | null }
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
  const budget = req.budget_limit_per_person ?? 4500;
  const headcount = req.serving_count ?? 320;

  // 응답에 등장하는 끼니를 표준 순서(아침→점심→저녁)로 정렬해 MealKind 로 변환.
  const mealKr = (r.applied_targets?.meals?.length ? r.applied_targets.meals
    : Array.from(new Set(Object.values(plan).flatMap((d) => Object.keys(d)))));
  const meals: MealKind[] = MEAL_ORDER.filter((m) => mealKr.includes(m)).map((m) => MEAL_FROM_KR[m]);

  const sodiumTarget = r.applied_targets?.sodium_max_mg_per_day ?? req.sodium_max_mg_per_day ?? null;
  const perMealSodiumCap = sodiumTarget ? sodiumTarget / Math.max(1, meals.length) : null;

  const buildCell = (kind: MealKind, names: string[], alt: boolean): MealCell => {
    let kcal = 0, protein = 0, cost = 0, sodium = 0;
    const items: MealItem[] = names.map((name) => {
      const n = nutri[name] ?? {};
      kcal += n.kcal ?? 0; protein += n.protein ?? 0; cost += n.cost ?? 0; sodium += n.sodium ?? 0;
      return { menuId: ++_mid, name, alt: alt || undefined };
    });
    // 셀 경고: 원가가 1식 예산 초과 또는 나트륨이 1식 상한 초과면 대표 메뉴에 태그.
    let flag: CellFlag | undefined;
    if (cost > budget) flag = '원가';
    else if (perMealSodiumCap && sodium > perMealSodiumCap) flag = '나트륨';
    if (flag && items.length) {
      const idx = Math.min(2, items.length - 1);
      items[idx] = { ...items[idx], flag };
    }
    return { kind, items, kcal: Math.round(kcal), protein: Math.round(protein * 10) / 10, warn: !!flag };
  };

  const weeksFrom = (planObj: Record<string, Record<string, string[]>>, alt: boolean): WeekBlock[] => {
    const keys = Object.keys(planObj).sort((a, b) => Number(a) - Number(b));
    const blocks: WeekBlock[] = [];
    keys.forEach((dayKey, i) => {
      const { label, dow, week } = dateFor(i);
      if (!blocks[week]) blocks[week] = { label: `${week + 1}주차`, days: [] };
      const dayPlan = planObj[dayKey] ?? {};
      const cells = meals.map((k) => {
        const kr = MEAL_ORDER.find((o) => MEAL_FROM_KR[o] === k)!;
        return buildCell(k, dayPlan[kr] ?? [], alt);
      });
      blocks[week].days.push({ date: label, dow, cells });
    });
    return blocks.filter(Boolean);
  };

  _mid = 0;
  _start = firstWeekday(new Date());
  const weeks = weeksFrom(plan, false);
  const alternatives: AltTrack[] = (r.alternatives ?? []).map((a) => ({
    label: a.group?.label ?? '대체식', count: a.group?.count ?? 0, weeks: weeksFrom(a.plan ?? {}, true),
  }));

  // 달성률: 열량은 daily_kcal 평균/목표, 단백질·나트륨은 plan 전체 합의 하루 평균/목표.
  const dayKeys = Object.keys(plan).sort((a, b) => Number(a) - Number(b));
  const days = dayKeys.length;
  const kcalTarget = r.applied_targets?.target_kcal_per_day ?? req.target_kcal_per_day ?? 1;
  const dkv = Object.values(r.daily_kcal ?? {});
  const avgKcal = dkv.length ? dkv.reduce((s, v) => s + v, 0) / dkv.length : 0;
  let protSum = 0, sodSum = 0;
  dayKeys.forEach((dk) => Object.values(plan[dk] ?? {}).forEach((names) => names.forEach((name) => {
    const n = nutri[name] ?? {}; protSum += n.protein ?? 0; sodSum += n.sodium ?? 0;
  })));
  // 단백질 목표: 백엔드가 프로파일에서 산출한 값. 프로파일 없이 요청한 경우에만 열량의 15%(4kcal/g)로 근사.
  const proteinTarget = r.applied_targets?.protein_g ?? Math.max(1, Math.round((kcalTarget * 0.15) / 4));
  const achievement: Achievement = {
    calories: { value: Math.round(avgKcal), target: Math.round(kcalTarget), unit: 'kcal' },
    protein: { value: days ? Math.round((protSum / days) * 10) / 10 : 0, target: proteinTarget, unit: 'g' },
    sodium: { value: days ? Math.round(sodSum / days) : 0, target: Math.round(sodiumTarget ?? (days ? sodSum / days : 0)), unit: 'mg' },
  };

  // 1인 원가: total_cost_won 은 1인 지평 총액 → 1식 기준으로 환산해 예산과 비교.
  const totalPerPerson = r.total_cost_won ?? 0;
  const perMealCost = days && meals.length ? Math.round(totalPerPerson / (days * meals.length)) : totalPerPerson;

  const profileLabel = PROFILE_LABEL[req.profile_key ?? ''] ?? (req.profile_key ?? '대상');
  const mealsText = meals.map((m) => MEAL_TABLE[m]).join('·');
  const groups = req.allergy_groups?.length ?? 0;

  const overCost = weeks.reduce((s, w) => s + w.days.filter((d) => d.cells.some((c) => c.items.some((it) => it.flag === '원가'))).length, 0);
  const overSod = weeks.reduce((s, w) => s + w.days.filter((d) => d.cells.some((c) => c.items.some((it) => it.flag === '나트륨'))).length, 0);
  const checks: PlanCheck[] = [];
  if (overCost) checks.push({ label: `원가 초과 ${overCost}일 확인`, done: false });
  if (overSod) checks.push({ label: `나트륨 초과 ${overSod}일 확인`, done: false });
  alternatives.forEach((t) => checks.push({ label: `대체식 '${t.label}' 검토`, done: false, view: true }));
  if (!checks.length) checks.push({ label: '검토할 경고 없음', done: true });

  return {
    conditionText: `${profileLabel} · ${headcount}명 · 평일 ${days}일 · ${mealsText} · 알레르기 ${groups}그룹 · 예산 ${budget.toLocaleString()}원/식`,
    periodText: `평일 ${days}일`, headcount, meals, weeks, alternatives,
    achievement, costPerPerson: perMealCost, budgetPerPerson: budget,
    totalDays: days, totalCost: totalPerPerson * headcount,
    checks,
    rationale: [r.status ? `solver: ${r.status}` : '', '열량 목표 대비 산출', groups ? `대체식 ${groups}그룹 파생` : ''].filter(Boolean),
    source: 'live',
    menuRecipes: r.menu_recipes,
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

/* 교체 후보 (팝오버) — 같은 자리(밥/국/주요리/반찬/김치)에서만 대안 제시.
   실제로는 모듈3 CSP 가 예산·나트륨 제약 안에서 후보를 반환할 자리(현재 503 → 카테고리별 목업). */
export interface SwapCandidate { name: string; kcal: number; cost: number; sodium: number }
type MenuCat = '밥' | '국' | '주요리' | '반찬' | '김치';
const CAT_POOL: Record<MenuCat, string[]> = {
  밥: ['잡곡밥', '기장밥', '흑미밥', '보리밥', '백미밥', '현미밥', '귀리밥', '수수밥'],
  국: ['미역국', '된장국', '북엇국', '콩나물국', '무국', '감자국', '유부장국', '시금치된장국'],
  주요리: ['제육볶음', '불고기', '돼지고기 장조림', '닭갈비', '고등어조림', '너비아니', '돈까스', '코다리조림', '두부조림', '생선구이'],
  반찬: ['시금치나물', '콩나물무침', '숙주나물', '오이무침', '가지볶음', '연근조림', '감자조림', '두부구이', '도라지무침'],
  김치: ['배추김치', '깍두기', '총각김치', '열무김치', '겉절이'],
};
function categoryOf(name: string): MenuCat {
  if (name.endsWith('밥')) return '밥';
  if (/(국|찌개|탕|개장)$/.test(name)) return '국';
  if (/(김치|깍두기|겉절이|단무지)/.test(name)) return '김치';
  if (/(나물|무침|샐러드|장아찌)/.test(name) || name === '두부구이') return '반찬';
  return '주요리';
}
export function swapCandidates(name: string): { sub: string; list: SwapCandidate[] } {
  if (name === '불고기') {
    return {
      sub: '예산 -410원 이내 · 나트륨 유지 대안',
      list: [
        { name: '돼지고기 장조림', kcal: 268, cost: 3980, sodium: 720 },
        { name: '닭갈비', kcal: 302, cost: 4120, sodium: 880 },
        { name: '두부조림', kcal: 184, cost: 3240, sodium: 640 },
      ],
    };
  }
  const cat = categoryOf(name);
  const others = CAT_POOL[cat].filter((n) => n !== name);
  const h = [...name].reduce((a, c) => a + c.charCodeAt(0), 0);
  const base = cat === '밥' ? 300 : cat === '국' ? 60 : cat === '김치' ? 15 : cat === '반찬' ? 90 : 220;
  const list = [0, 1, 2].map((k) => {
    const n = others[(h + k * 3) % others.length];
    return { name: n, kcal: base + ((h + k) % 6) * 12, cost: 2600 + ((h + k) % 6) * 180, sodium: 300 + ((h + k) % 6) * 70 };
  });
  return { sub: `같은 자리(${cat}) 안에서 예산·나트륨 유지 대안`, list };
}

export const PROFILE_OPTIONS = [
  { value: 'elem_low_mix', label: '초등학생', age: '만 6–11세 · 2020 한국인 영양섭취기준' },
  { value: 'middle_mix', label: '중학생', age: '만 12–14세 · 2020 한국인 영양섭취기준' },
  { value: 'high_mix', label: '고등학생', age: '만 15–17세 · 2020 한국인 영양섭취기준' },
  { value: 'senior_mix', label: '노인', age: '만 65세 이상 · 2020 한국인 영양섭취기준' },
];
export const ALLERGEN_POOL = ['난류', '우유', '땅콩', '대두', '밀', '갑각류', '고등어', '새우', '복숭아', '토마토'];
