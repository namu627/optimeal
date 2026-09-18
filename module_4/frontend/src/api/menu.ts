// src/api/menu.ts
// 식단 생성(모듈 3 CSP) API 래퍼 + 프론트 뷰모델 + 목업.
// 백엔드 generate 가 아직 503(ortools 미설치 · CSP 세 브랜치 미병합)이라, 실제 plan 응답이 확정되기
// 전까지 UI 는 뷰모델(MealPlan)만 바인딩한다. 통합되면 toMealPlan() 한 함수만 실제 응답에 맞춰 고치면 됨.
import { api } from './client';

/* ────────────── 백엔드 계약 (Swagger) ────────────── */

export interface MenuProfile {
  profile_key: string;
  label?: string;
  target_kcal_per_day?: number;
  sodium_max_mg_per_day?: number;
  source?: string;
  note?: string;
  [k: string]: unknown;
}

export interface AllergyGroup {
  label: string;
  allergens: string[];
  count: number;
}

export interface MenuGenerateRequest {
  profile_key?: string;
  days: number;
  meals?: string[]; // ["점심"] / ["점심","저녁"] / ["아침","점심","저녁"]
  target_kcal_per_day?: number;
  kcal_tolerance?: number;
  budget_limit_per_person?: number | null;
  sodium_max_mg_per_day?: number | null;
  with_alternatives?: boolean;
  allergy_groups?: AllergyGroup[];
  exclude_menu_ids?: number[]; // 영양사가 뺀 메뉴
  include_menu_ids?: number[]; // 영양사가 넣은 메뉴
  solver_time_limit?: number;
}

/** generate 원시 응답 — plan 구조가 느슨해 unknown 취급, 매핑은 toMealPlan 에서 */
export interface MenuGenerateRaw {
  status?: string;
  plan?: unknown;
  daily_kcal?: unknown;
  total_cost?: number;
  alternatives?: unknown;
  [k: string]: unknown;
}

export async function listProfiles(): Promise<MenuProfile[]> {
  const { data } = await api.get<MenuProfile[]>('/api/menu/profiles');
  return data;
}
export async function generateMenu(body: MenuGenerateRequest): Promise<MenuGenerateRaw> {
  const { data } = await api.post<MenuGenerateRaw>('/api/menu/generate', body);
  return data;
}
export async function getMenu(id: number): Promise<MenuGenerateRaw> {
  const { data } = await api.get<MenuGenerateRaw>(`/api/menu/${id}`);
  return data;
}
export function isUnavailable(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 503;
}

/* ────────────── 프론트 뷰모델 (UI 는 이것만 바인딩) ────────────── */

export type MealKind = 'breakfast' | 'lunch' | 'dinner';
export const MEAL_KR: Record<MealKind, string> = { breakfast: '아침', lunch: '점심', dinner: '저녁' };
export const MEAL_DOT: Record<MealKind, string> = { breakfast: '#F5B301', lunch: '#06B6D4', dinner: '#8B5CF6' };

export type CellFlag = '나트륨' | '원가' | '대체';
export interface MealItem { menuId?: number; name: string; kcal?: number; flags?: CellFlag[]; }
export interface MealCell {
  kind: MealKind;
  items: MealItem[];
  kcal: number;
  costPerPerson?: number;
  sodiumMg?: number;
  warn?: boolean; // 예산·나트륨 초과
  note?: string; // 셀 하단 메타 (원가/나트륨)
}
export interface MealDay { date: string; dow: string; cells: MealCell[]; totalKcal: number; anyWarn: boolean; }
export interface AltTrack { label: string; count: number; days: MealDay[]; }

export interface MealPlan {
  menuId?: number | null;
  targetLabel: string; // "elem_low_mix · 알레르기 그룹 포함"
  periodLabel: string; // "9월 3주차 · 점심"
  meals: MealKind[];
  days: MealDay[];
  alternatives: AltTrack[]; // 대체식 트랙 (알레르기 그룹별)
  rationale: string[]; // 생성 근거 (hard/soft breakdown 요약)
  source: 'live' | 'mock';
}

/**
 * 실제 generate 응답 → MealPlan.
 * ⚠️ 백엔드 generate 의 plan/alternatives/breakdown 실제 JSON 이 확정되면 여기만 채운다.
 * 지금은 미구현이라 예외를 던지고, 페이지는 mockPlan 으로 폴백한다.
 */
export function toMealPlan(_raw: MenuGenerateRaw, _req: MenuGenerateRequest): MealPlan {
  throw new Error('toMealPlan: 실제 generate 응답 스키마 확정 후 매핑 구현 필요');
}

/* ────────────── 목업 (백엔드 통합 전 화면 확인용) ────────────── */

const KOR_MEALS: Record<MealKind, string[][]> = {
  breakfast: [
    ['백미밥', '북엇국', '달걀찜'], ['잡곡밥', '된장국', '김구이'], ['흑미밥', '감자국', '메추리알조림'],
    ['백미밥', '미소국', '두부부침'], ['보리밥', '황태국', '김치볶음'], ['백미밥', '무국', '달걀말이'],
    ['기장밥', '콩나물국', '김구이'],
  ],
  lunch: [
    ['잡곡밥', '미역국', '제육볶음', '시금치나물'], ['기장밥', '김치찌개', '계란말이', '콩나물무침'],
    ['흑미밥', '된장국', '불고기', '숙주나물'], ['보리밥', '시금치된장국', '생선까스', '배추김치'],
    ['잡곡밥', '유부장국', '돼지갈비찜', '두부구이'], ['현미밥', '콩나물국', '닭볶음탕', '오이무침'],
    ['기장밥', '감자국', '고등어구이', '가지볶음'],
  ],
  dinner: [
    ['백미밥', '육개장', '고등어조림'], ['잡곡밥', '순두부찌개', '닭갈비'], ['흑미밥', '설렁탕', '겉절이'],
    ['보리밥', '어묵탕', '돈까스'], ['백미밥', '김치찜', '코다리조림'], ['현미밥', '된장찌개', '제육볶음'],
    ['기장밥', '미역국', '너비아니'],
  ],
};
const DOW = ['월', '화', '수', '목', '금', '토', '일'];
const ALT_SUBS = ['두부조림', '메추리알장조림', '채소볶음', '감자조림', '어묵볶음'];
const SWAP_POOL = ['돼지고기 장조림', '닭갈비', '두부조림', '생선구이', '연근조림', '감자조림'];

let _mid = 1000;
const item = (name: string, kcal: number, flags?: CellFlag[]): MealItem => ({ menuId: _mid++, name, kcal, flags });

// 메뉴 위치별 대략 칼로리 (주식/국/주찬/부찬 순) — 목업용
const KCAL_POS: Record<MealKind, number[]> = {
  breakfast: [300, 80, 150],
  lunch: [310, 90, 300, 60],
  dinner: [320, 110, 300],
};

function mockDay(i: number, meals: MealKind[], startDate: number, alt = false, seed = 0): MealDay {
  const idx = (i + seed) % 7;
  const cells: MealCell[] = meals.map((kind) => {
    const base = KOR_MEALS[kind][idx];
    // 데모용 경고: 일반식에서만 특정 요일에 나트륨/원가 초과 하나씩
    const naWarn = !alt && kind === 'lunch' && idx === 1;
    const costWarn = !alt && kind === 'lunch' && idx === 2;
    // 대체식은 마지막 반찬을 대체 메뉴로 교체 → 일반식과 눈에 띄게 다름
    const names = alt ? [...base.slice(0, -1), ALT_SUBS[i % ALT_SUBS.length]] : base;
    const posK = KCAL_POS[kind];
    const items = names.map((n, j) => {
      const flags: CellFlag[] = [];
      if (alt && j === names.length - 1) flags.push('대체');
      if (naWarn && j === 1) flags.push('나트륨');
      if (costWarn && j === 2) flags.push('원가');
      const k = (posK[j] ?? 120) + ((idx + j) % 3) * 8; // 요일별 약간 변동
      return item(n, k, flags.length ? flags : undefined);
    });
    const kcal = items.reduce((s, it) => s + (it.kcal ?? 0), 0); // 끼니 kcal = 메뉴 합
    return {
      kind, items, kcal,
      costPerPerson: costWarn ? 4910 : 4200 + (idx % 4) * 40,
      sodiumMg: naWarn ? 1480 : 900 + (idx % 5) * 30,
      warn: naWarn || costWarn,
      note: naWarn ? '나트륨 1,480mg (초과)' : costWarn ? '1인 원가 4,910원 (예산 초과)' : `1인 원가 ${(4200 + (idx % 4) * 40).toLocaleString()}원`,
    };
  });
  const totalKcal = cells.reduce((s, c) => s + c.kcal, 0);
  return { date: `9/${startDate + i}`, dow: DOW[i % 7], cells, totalKcal, anyWarn: cells.some((c) => c.warn) };
}

/** seed 를 바꾸면 메뉴가 회전해 '재생성'이 눈에 보이게 달라진다 */
export function mockPlan(req: MenuGenerateRequest, seed = 0): MealPlan {
  _mid = 1000 + seed * 500;
  const meals: MealKind[] = (req.meals?.length
    ? req.meals.map((m) => (m === '아침' ? 'breakfast' : m === '저녁' ? 'dinner' : 'lunch'))
    : ['lunch']) as MealKind[];
  const n = Math.min(req.days ?? 7, 7);
  const days = Array.from({ length: n }, (_, i) => mockDay(i, meals, 15, false, seed));
  const alternatives: AltTrack[] = (req.allergy_groups ?? []).map((g) => ({
    label: g.label,
    count: g.count,
    days: Array.from({ length: n }, (_, i) => mockDay(i, meals, 15, true, seed)),
  }));
  const totalAllergy = req.allergy_groups?.reduce((s, g) => s + g.count, 0) ?? 0;
  return {
    menuId: null,
    targetLabel: `${req.profile_key ?? '대상 미지정'}${totalAllergy ? ` · 알레르기 ${totalAllergy}명` : ''}`,
    periodLabel: `9월 3주차 · ${meals.map((m) => MEAL_KR[m]).join('·')}`,
    meals,
    days,
    alternatives,
    rationale: [
      '열량 목표 ±10% 충족',
      '동일 메뉴 3일 내 재등장 없음',
      '예산 한도 이내',
      req.allergy_groups?.length ? `대체식 ${req.allergy_groups.length}그룹 분리` : '알레르기 그룹 없음',
    ],
    source: 'mock',
  };
}

/** SWAP 후보 (교체 팝오버용) — 목업. 실제로는 후보 메뉴 조회 API 로 대체 */
export function swapCandidates(): string[] {
  return SWAP_POOL.slice(0, 3);
}
