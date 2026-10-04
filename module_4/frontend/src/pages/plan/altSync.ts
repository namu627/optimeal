// src/pages/plan/altSync.ts
// 검토 화면에서 일반식 칸을 교체·되돌리기·삭제하면 대체식 트랙의 같은 칸(같은 주·날·끼니)에도 반영한다(UI 없음).
//   (a) 대체식 칸에 일반식의 그 메뉴(편집 전 이름)가 그대로 있으면 = 알레르기로 대체되지 않은 자리 → 같은 편집을 따라간다.
//       칸에 '일반식 교체 반영'(altFollow)을 남겨 대체식 표(PDF)에도 그 끼니가 실린다. 되돌리면 걷는다.
//   (b) 없으면 = 그 자리는 알레르기 때문에 다른 메뉴로 대체됨 → 대체식은 그대로 두고 칸에 '대체식 재검토 필요'를 단다.
//       일반식을 원래 메뉴로 되돌리면 그 표시는 걷는다(원래 메뉴 기준으로 만든 대체가 다시 유효).
// 표시(MealCell.altReview·altFollow)는 plan JSON 에 실리므로 저장·불러오기·CSV·PDF 가 그대로 쓴다.
// editPlan 이 검토 화면(Step2Review)의 교체·되돌리기·삭제 진입점 — 테스트도 이 함수로 화면과 같은 경로를 탄다.
import { recomputePlan, type AltReview, type MealCell, type MealItem, type MealPlan, type WeekBlock } from '../../api/menu';

export type MainEdit =
  | { kind: 'swap'; to: Pick<MealItem, 'name' | 'nutritionId' | 'nutri'> }
  | { kind: 'revert' }
  | { kind: 'delete' };

export const ALT_REVIEW_LABEL = '대체식 재검토 필요';
export const ALT_FOLLOW_LABEL = '일반식 교체 반영';

/** 편집 하나를 메뉴 한 개에 적용(일반식 원본·대체식 추종 메뉴·대체식 직접 편집 공통). null = 삭제. */
function applyEdit(x: MealItem, e: MainEdit, isMain: boolean): MealItem | null {
  if (e.kind === 'delete') return null;
  if (e.kind === 'revert') {
    return {
      ...x, name: x.orig ?? x.name, nutritionId: x.origNutritionId, nutri: x.origNutri, flag: undefined,
      orig: undefined, origNutritionId: undefined, origNutri: undefined,
    };
  }
  const first = x.orig ? { orig: x.orig, origNutritionId: x.origNutritionId, origNutri: x.origNutri }
    : { orig: x.name, origNutritionId: x.nutritionId, origNutri: x.nutri };
  return { ...x, ...e.to, flag: undefined, ...(isMain ? { alt: false } : {}), ...first };
}

function mapCell(weeks: WeekBlock[], w: number, d: number, ci: number, fn: (c: MealCell) => MealCell): WeekBlock[] {
  return weeks.map((wk, wi) => (wi !== w ? wk : {
    ...wk, days: wk.days.map((day, di) => (di !== d ? day : { ...day, cells: day.cells.map((c, i) => (i !== ci ? c : fn(c))) })),
  }));
}

const describe = (from: string, e: MainEdit, to: string) =>
  e.kind === 'delete' ? `일반식 '${from}' 삭제` : `일반식 '${from}' → '${to}' ${e.kind === 'revert' ? '되돌리기' : '교체'}`;

/** 같은 원인(mainMenuId)의 표시를 새 것으로 바꾸거나(note) 걷는다(null). 비면 undefined. */
function setNote(list: AltReview[] | undefined, menuId: number, note: string | null): AltReview[] | undefined {
  const rest = (list ?? []).filter((r) => r.mainMenuId !== menuId);
  const next = note == null ? rest : [...rest, { mainMenuId: menuId, note }];
  return next.length ? next : undefined;
}

/**
 * 일반식 메뉴(menuId) 편집을 일반식과 모든 대체식 트랙에 적용한다. 일반식에 없는 menuId(대체식 탭에서 대체식 메뉴를 직접
 * 편집)면 null — 호출부(editPlan)가 그 메뉴만 고친다. 셀 합계는 호출부의 recomputePlan 이 다시 계산한다.
 */
export function applyMainEdit(plan: MealPlan, menuId: number | undefined, e: MainEdit): MealPlan | null {
  if (menuId == null) return null;
  let at: { w: number; d: number; ci: number; item: MealItem } | null = null;
  plan.weeks.forEach((wk, w) => wk.days.forEach((day, d) => day.cells.forEach((c, ci) => {
    const item = c.items.find((x) => x.menuId === menuId);
    if (item && !at) at = { w, d, ci, item };
  })));
  if (!at) return null;
  const { w, d, ci, item } = at as { w: number; d: number; ci: number; item: MealItem };
  const kind = plan.weeks[w].days[d].cells[ci].kind;
  const edited = applyEdit(item, e, true);
  const weeks = mapCell(plan.weeks, w, d, ci, (c) => ({
    ...c, items: c.items.flatMap((x) => (x.menuId !== menuId ? [x] : edited ? [edited] : [])),
  }));
  const origName = item.orig ?? item.name;   // 생성 당시 일반식 메뉴
  // 원래 메뉴로 돌아오면(되돌리기·원래 메뉴로 다시 교체) 이 메뉴 때문에 단 표시는 모두 걷는다.
  const backToOrig = e.kind === 'revert' || (e.kind === 'swap' && e.to.name === origName);
  const note = backToOrig ? null : describe(origName, e, edited?.name ?? '');
  const alternatives = plan.alternatives.map((t) => {
    const aci = t.weeks[w]?.days[d]?.cells.findIndex((c) => c.kind === kind) ?? -1;
    if (aci < 0) return t;
    return {
      ...t, weeks: mapCell(t.weeks, w, d, aci, (c) => {
        const follower = c.items.find((x) => x.name === item.name);
        if (follower) {   // (a) 대체되지 않은 자리 → 따라간다
          const next = applyEdit(follower, e, false);
          return {
            ...c, items: c.items.flatMap((x) => (x !== follower ? [x] : next ? [next] : [])),
            altFollow: setNote(c.altFollow, menuId, note),
          };
        }
        // (b) 알레르기 대체 자리 → 그대로 두고 표시. 원래 메뉴로 되돌아오면 표시 해제.
        return { ...c, altReview: setNote(c.altReview, menuId, note) };
      }),
    };
  });
  return { ...plan, weeks, alternatives };
}

/** 메뉴(칸 식별자 menuId)가 들어 있는 트랙들에서만 그 메뉴를 고친다 — 대체식 탭에서 대체식 메뉴를 직접 편집할 때. */
function editOwnItem(plan: MealPlan, menuId: number | undefined, e: MainEdit): MealPlan {
  const mapW = (weeks: WeekBlock[]): WeekBlock[] => weeks.map((wk) => ({
    ...wk, days: wk.days.map((day) => ({ ...day, cells: day.cells.map((c) => ({
      ...c, items: c.items.flatMap((x) => {
        if (x.menuId !== menuId) return [x];
        const next = applyEdit(x, e, true);
        return next ? [next] : [];
      }),
    })) })),
  }));
  return { ...plan, weeks: mapW(plan.weeks), alternatives: plan.alternatives.map((t) => ({ ...t, weeks: mapW(t.weeks) })) };
}

/**
 * 검토 화면의 교체·되돌리기·삭제 한 번 — 일반식 메뉴면 대체식 트랙까지 반영(applyMainEdit), 대체식 메뉴면 그 메뉴만.
 * 결과는 셀·총합·달성률·원가를 칸 영양으로 다시 계산한 plan(recomputePlan). 이 plan 이 그대로 확정·저장·CSV·PDF 로 간다.
 * ⚠ 제약 재검증(3일 중복·열량 밴드 등)은 하지 않는다 — 재생성(include/exclude 재풀이)은 후속.
 */
export function editPlan(plan: MealPlan, item: Pick<MealItem, 'menuId'>, e: MainEdit): MealPlan {
  return recomputePlan(applyMainEdit(plan, item.menuId, e) ?? editOwnItem(plan, item.menuId, e));
}

/** 칸의 재검토 표시 문구(없으면 ''). 표·CSV·화면 공통. */
export function altReviewText(c: Pick<MealCell, 'altReview'> | undefined): string {
  return c?.altReview?.length ? `${ALT_REVIEW_LABEL}(${c.altReview.map((r) => r.note).join(' · ')})` : '';
}

/** 칸의 '일반식 교체 반영' 문구(없으면 ''). 대체식 표 공통. */
export function altFollowText(c: Pick<MealCell, 'altFollow'> | undefined): string {
  return c?.altFollow?.length ? `${ALT_FOLLOW_LABEL}(${c.altFollow.map((r) => r.note).join(' · ')})` : '';
}
