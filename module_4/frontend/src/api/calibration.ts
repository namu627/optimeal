// src/api/calibration.ts
// 캘리브레이션 API 래퍼 (화면 9). 백엔드 계약: module_4/backend/src/routers/calibration.py (ADR-008 §2.2)
//  - 업장 목록(listSites)·레시피·스케일링(predictScaling)은 api/scaling.ts 것을 그대로 쓴다.
//  - 캘리브레이션 API 는 SQLite(calibration.db) 기반이라 PostgreSQL 없이도 동작한다.
import { api } from './client';
import type { SiteItem } from './scaling';

/** POST /api/calibration/sites — site_id 는 서버가 발급하지 않으므로 프론트가 정한다(nextSiteId). */
export interface SiteCreateBody {
  site_id: number;
  site_name: string;
  site_type: string;
}

/** POST /api/calibration/observations — corrected_g 가 '관측된 정답'(영양사 확정 투입량). */
export interface ObservationBody {
  site_id: number;
  recipe_id: number;
  ingredient_id: number;
  n_target: number;
  base_amount_g: number; // 1인분 투입량(g), 0 초과
  corrected_g: number;   // 실제 사용량(g), 0 이상
  cbr_shown?: boolean;   // CBR 참고 이력을 보여준 상태였는지(파일럿 A/B)
}

export interface EstimateOut {
  site_id: number;
  recipe_id: number;
  ingredient_id: number;
  est_ratio: number;
  n_obs: number;
  confidence: 'cold_start' | 'low' | 'high';
  method: 'cold_start_linear' | 'calibrated';
  updated_at?: string | null;
}

export interface ObservationOut {
  accepted: boolean;
  round_no: number;
  estimate: EstimateOut;
  suggested_g: number;
  note: string;
}

/** 수렴 곡선 1점. ape_pct = |직전 추정 − 실측| / 실측 × 100. 실측이 0g 이면 null 로 온다. */
export interface ConvergencePoint {
  round_no: number;
  obs_ratio: number;
  prior_ratio: number;
  ape_pct: number | null;
}

export interface ConvergenceOut {
  site_id: number;
  recipe_id: number;
  ingredient_id: number;
  points: ConvergencePoint[];
  n_obs: number;
  caveat: string;
}

/** CBR 참고 이력 1건 — 표시 전용(auto_apply 는 항상 false). */
export interface CbrReference {
  site_id: number;
  recipe_id: number;
  jaccard: number;
  est_ratio: number;
  n_obs: number;
  updated_at?: string | null;
}

export interface CbrOut {
  target_ingredient_id: number;
  references: CbrReference[];
  auto_apply: boolean;
  disclaimer: string;
}

export async function createSite(body: SiteCreateBody): Promise<void> {
  await api.post('/api/calibration/sites', body);
}

export async function recordObservation(body: ObservationBody): Promise<ObservationOut> {
  const { data } = await api.post<ObservationOut>('/api/calibration/observations', body);
  return data;
}

export async function getConvergence(siteId: number, recipeId: number, ingredientId: number): Promise<ConvergenceOut> {
  const { data } = await api.get<ConvergenceOut>('/api/calibration/convergence', {
    params: { site_id: siteId, recipe_id: recipeId, ingredient_id: ingredientId },
  });
  return data;
}

/** 재료구성이 비슷한 과거 보정 이력. ingredientIds = 지금 레시피의 전체 재료 id(Jaccard 입력). */
export async function getReferences(targetIngredientId: number, ingredientIds: number[], topK = 5): Promise<CbrOut> {
  const { data } = await api.get<CbrOut>('/api/calibration/references', {
    params: { target_ingredient_id: targetIngredientId, ingredient_ids: ingredientIds.join(','), top_k: topK },
  });
  return data;
}

/** 새 업장 id — 등록된 id 최댓값 + 1 (백엔드는 같은 id 재등록을 갱신으로 처리하므로 겹치지 않게). */
export function nextSiteId(sites: SiteItem[]): number {
  return sites.reduce((max, s) => Math.max(max, s.site_id), 0) + 1;
}

// ── 업장 기준 인원 ──────────────────────────────────────────────────────────
// 백엔드 업장 스키마에는 기준 인원 필드가 없다(site_id·site_name·site_type 뿐).
// 시안 09a 의 '기준 인원'은 브라우저 로컬에만 저장해 보정값 입력의 인원수 기본값으로 쓴다(컨벤션 §6).
const HEADCOUNT_KEY = 'optimeal:calibration:site-headcount';

function readHeadcounts(): Record<string, number> {
  try {
    const raw = localStorage.getItem(HEADCOUNT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, number>) : {};
  } catch {
    return {};
  }
}

export function getSiteHeadcount(siteId: number): number | undefined {
  const v = readHeadcounts()[String(siteId)];
  return typeof v === 'number' && v >= 1 ? v : undefined;
}

export function setSiteHeadcount(siteId: number, headcount: number): void {
  try {
    localStorage.setItem(HEADCOUNT_KEY, JSON.stringify({ ...readHeadcounts(), [String(siteId)]: headcount }));
  } catch {
    /* 저장 실패(사생활 보호 모드 등)는 무시 — 기본 인원수만 못 쓸 뿐 */
  }
}