// src/api/scaling.ts
// 레시피 스케일링 + 업장(캘리브레이션) 관련 API 래퍼.
// 백엔드 계약 기준: OptiMeal API Swagger (권성민), ADR-008.
//  - cold-start 는 선형(base×N). site_id 를 주면 그 업장에 누적된 보정이 있는 재료만 calibrated.
//  - 스케일링/캘리브레이션 API 는 CSV 기반이라 DB 없이도 동작한다.
import { api } from './client';

/** GET /api/scaling/recipes 목록 원소 */
export interface RecipeListItem {
  recipe_key: string;      // 예: "A1034"  (predict 입력 키)
  recipe_id: number;       // 캘리브레이션용 정수 id
  group_type: string;      // dry_heat | moist_heat | no_heat
  cooking_method: string;
  n_ingredients: number;
  recipe_name?: string;    // 백엔드가 메뉴명을 실어 보내면 채워짐(현재 미제공 → 옵셔널)
}

/** GET /api/scaling/recipes/{recipe_key} 의 1인분 재료 (스키마가 느슨해 방어적으로 정의) */
export interface RecipeBaseIngredient {
  ingredient_id: number;
  ingredient_name: string;
  role: string;
  base_amount_g: number;
}
export interface RecipeDetail {
  recipe_key: string;
  recipe_id: number;
  group_type: string;
  cooking_method: string;
  ingredients: RecipeBaseIngredient[];
  [k: string]: unknown;
}

/** POST /api/scaling/predict 응답의 재료 1건 (근거·신뢰도 동반) */
export interface ScaledIngredient {
  ingredient_id: number;
  ingredient_name: string;
  role: string;
  base_amount_g: number;
  scaled_g: number;
  ratio: number;
  method: 'cold_start_linear' | 'calibrated';
  n_obs: number;
  confidence: 'cold_start' | 'low' | 'high';
  cbr_references?: unknown[];
}

/** POST /api/scaling/predict 응답 */
export interface ScalingResponse {
  recipe_key: string;
  recipe_id: number;
  group_type: string;
  cooking_method: string;
  n_target: number;
  site_id: number | null;
  ingredients: ScaledIngredient[];
  calibrated_count: number;
  cold_start_count: number;
  disclaimer: string;
}

export interface PredictBody {
  recipe_key: string;
  n_target: number;
  site_id?: number | null;
  include_cbr?: boolean;
  cbr_top_k?: number;
}

/** GET /api/calibration/sites 원소 (top-level 래핑 없음, count 없음) */
export interface SiteItem {
  site_id: number;
  site_name: string;
  site_type: string;
}

export async function listRecipes(q = '', limit = 50, offset = 0): Promise<RecipeListItem[]> {
  const { data } = await api.get<RecipeListItem[]>('/api/scaling/recipes', {
    params: { q: q || undefined, limit, offset },
  });
  return data;
}

export async function getRecipe(recipeKey: string): Promise<RecipeDetail> {
  const { data } = await api.get<RecipeDetail>(`/api/scaling/recipes/${encodeURIComponent(recipeKey)}`);
  return data;
}

export async function predictScaling(body: PredictBody): Promise<ScalingResponse> {
  const { data } = await api.post<ScalingResponse>('/api/scaling/predict', body);
  return data;
}

export async function listSites(): Promise<SiteItem[]> {
  const { data } = await api.get<SiteItem[]>('/api/calibration/sites');
  return data;
}
