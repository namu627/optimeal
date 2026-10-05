// src/pages/plan/cloneForm.ts
// 식단 목록 '복제해서 만들기' — 저장된 식단(MealPlan 뷰모델)을 1단계 조건 폼 값으로 되돌린다.
// 뷰모델에 남은 값으로 복원한다.
//  · 복원: 대상 프로파일(라벨 역매핑), 인원, 기간, 끼니, 1인 1식 예산·예산 방식, 나트륨 상한, 열량 목표
//  · 기저질환·알레르기 그룹: 생성할 때 식단에 같이 담아 둔 입력값(inputConds·inputGroups)으로 복원.
//    이 값이 없는 예전 저장본은 빈 값으로 두고 화면에서 안내한다.
import { MEAL_KR, PROFILE_OPTIONS, budgetModeOf, profileKeyOfLabel, type MealPlan, type AllergyGroup } from '../../api/menu';
import type { Step1Form } from './Step1Conditions';

const DAY_OPTIONS = [1, 7, 31];

// 식단(MealPlan)에 같이 담아 두는 입력값. 백엔드는 식단 JSON 을 그대로 저장·반환하므로 저장본에도 남는다.
// 2026-10-05 이전 저장본에는 없다.
type PlanInputs = {
  /** 생성할 때 입력한 기저질환(이름 → 인원) */
  inputConds?: Record<string, number | null>;
  /** 생성할 때 입력한 알레르기 그룹(항목·인원). 알레르기를 고르지 않은 빈 그룹은 뺀다. */
  inputGroups?: AllergyGroup[];
};

/** 생성 결과에 그때의 기저질환·알레르기 입력값을 붙인다(검토·교체·저장을 거쳐도 그대로 따라간다). */
export function attachInputs(plan: MealPlan, form: Step1Form): MealPlan {
  const inputs: PlanInputs = { inputConds: form.conds, inputGroups: form.groups.filter((g) => g.allergens.length) };
  return { ...plan, ...inputs };
}

/** 저장본에 기저질환·알레르기 입력값이 들어 있는지(없으면 복제 때 다시 입력해야 한다). */
export function hasInputs(plan: MealPlan): boolean {
  const p = plan as MealPlan & PlanInputs;
  return !!(p.inputConds || p.inputGroups);
}

export function planToStep1Form(plan: MealPlan): Step1Form {
  const { inputConds, inputGroups } = plan as MealPlan & PlanInputs;
  const target = plan.conditionText.split(' · ')[0];
  // 예전 저장본의 라벨('초등학생'·'노인' 등)도 profileKeyOfLabel 이 받아 준다
  const profile = profileKeyOfLabel(target) ?? PROFILE_OPTIONS[0].value;
  const days = DAY_OPTIONS.includes(plan.totalDays) ? plan.totalDays : 7;
  const meals = plan.meals.length ? plan.meals.map((m) => MEAL_KR[m]) : ['점심'];
  return {
    profile,
    count: plan.headcount,
    conds: { ...(inputConds ?? {}) },
    days,
    meals,
    kcal: plan.achievement?.calories?.target || 1750,
    sodium: plan.sodiumCapPerDay ?? 1300,
    budget: plan.budgetPerPerson,
    budgetMode: budgetModeOf(plan),
    groups: inputGroups?.length
      ? inputGroups.map((g, i) => ({ label: `그룹 ${i + 1}`, allergens: [...g.allergens], count: g.count }))
      : [{ label: '그룹 1', allergens: [], count: 0 }],
  };
}