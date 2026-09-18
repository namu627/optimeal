import type { CostPerServing, NutritionAchievement } from './KpiRow';

/**
 * 백엔드 연결 전까지 쓰는 목 데이터.
 * 시안 05(2단계 검토)·06(3단계 확정)의 수치 — 열량 97 / 단백질 104 / 나트륨 112, 1인 원가 4,320원.
 */
export const mockAchievement: NutritionAchievement = {
  calories: { value: 757, target: 780, unit: 'kcal' }, // 97% 적정
  protein: { value: 31.2, target: 30, unit: 'g' },     // 104% 적정
  sodium: { value: 1456, target: 1300, unit: 'mg' },   // 112% 초과
};

export const mockCost: CostPerServing = { value: 4320, budget: 4500 };

/** 부족(머스터드) 확인용 */
export const mockAchievementUnder: NutritionAchievement = {
  calories: { value: 640, target: 780, unit: 'kcal' }, // 82% 부족
  protein: { value: 29.1, target: 30, unit: 'g' },     // 97% 적정
  sodium: { value: 1456, target: 1300, unit: 'mg' },   // 112% 초과
};

/** 예산 초과 확인용 */
export const mockCostOverBudget: CostPerServing = { value: 4820, budget: 4500 };