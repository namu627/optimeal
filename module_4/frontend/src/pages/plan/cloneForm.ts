// src/pages/plan/cloneForm.ts
// 식단 목록 '복제해서 만들기' — 저장된 식단(MealPlan 뷰모델)을 1단계 조건 폼 값으로 되돌린다.
// 저장본에는 생성 요청 원본이 없어서 뷰모델에 남은 값만 복원한다.
//  · 복원: 대상 프로파일(라벨 역매핑), 인원, 기간, 끼니, 1인 1식 예산·예산 방식, 나트륨 상한, 열량 목표
//  · 복원 불가: 기저질환, 알레르기 항목(대체식 트랙에는 그룹 이름·인원만 남음) → 빈 값으로 두고 화면에서 안내
import { MEAL_KR, PROFILE_OPTIONS, budgetModeOf, profileKeyOfLabel, type MealPlan } from '../../api/menu';
import type { Step1Form } from './Step1Conditions';

const DAY_OPTIONS = [1, 7, 31];

export function planToStep1Form(plan: MealPlan): Step1Form {
  const target = plan.conditionText.split(' · ')[0];
  // 예전 저장본의 라벨('초등학생'·'노인' 등)도 profileKeyOfLabel 이 받아 준다
  const profile = profileKeyOfLabel(target) ?? PROFILE_OPTIONS[0].value;
  const days = DAY_OPTIONS.includes(plan.totalDays) ? plan.totalDays : 7;
  const meals = plan.meals.length ? plan.meals.map((m) => MEAL_KR[m]) : ['점심'];
  return {
    profile,
    count: plan.headcount,
    conds: {},
    days,
    meals,
    kcal: plan.achievement?.calories?.target || 1750,
    sodium: plan.sodiumCapPerDay ?? 1300,
    budget: plan.budgetPerPerson,
    budgetMode: budgetModeOf(plan),
    groups: [],
  };
}