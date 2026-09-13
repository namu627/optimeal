// src/api/nutrition.ts
// 영양성분 검색 API 래퍼.
// 백엔드 계약: GET /api/nutrition/search -> { count, results:[...] }
//   DB 미구성 시 503 + {reason,message,hint}. 프론트는 503 을 "인프라 준비중" 으로 처리.
import { api } from './client';

/** search 결과 행 (검색 응답이 돌려주는 컬럼만) */
export interface NutritionRow {
  nutrition_id: number;
  recipe_name: string;
  menu_category: string | null;
  calories: number;
  protein: number | null;
  fat: number | null;
  carbs: number | null;
  sodium: number | null;
}

export interface NutritionSearchResponse {
  count: number;
  results: NutritionRow[];
}

/** 상세 조회는 스키마가 느슨해 확장 필드를 옵셔널로 둔다 */
export interface NutritionDetail extends NutritionRow {
  serving_size_g?: number | null;
  sugar?: number | null;
  saturated_fat?: number | null;
  trans_fat?: number | null;
  cholesterol?: number | null;
  data_source?: string | null;
  [k: string]: unknown;
}

export interface SearchParams {
  q: string;
  menu_category?: string;
  limit?: number;
}

/** 503(DB 미구성)인지 판별 — 페이지에서 "인프라 준비중" 분기용 */
export function isUnavailable(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 503;
}

export async function searchNutrition(params: SearchParams): Promise<NutritionSearchResponse> {
  const { data } = await api.get<NutritionSearchResponse>('/api/nutrition/search', {
    params: {
      q: params.q,
      menu_category: params.menu_category || undefined,
      limit: params.limit ?? 50,
    },
  });
  return data;
}

export async function getNutrition(nutritionId: number): Promise<NutritionDetail> {
  const { data } = await api.get<NutritionDetail>(`/api/nutrition/${nutritionId}`);
  return data;
}
