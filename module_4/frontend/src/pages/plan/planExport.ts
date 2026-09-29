// src/pages/plan/planExport.ts
// 확정 식단 내려받기 — CSV(식단표·조리 지시서)와 PDF. 식단표 행은 CSV·PDF 가 같은 함수(tableRows)를 쓴다.
// PDF 조판은 백엔드(/api/menu/export/pdf, 한글 폰트 임베드)가 하고, 내용은 여기서 만든 행 그대로다.
import {
  MEAL_TABLE, exportPlanPdf, planDateRange, planTargetLabel,
  type MealPlan, type MealKind, type MenuRecipe, type PdfExportRequest,
} from '../../api/menu';
import { collectEntries, fetchMissingRecipes, fetchedRecipe, missingRecipes, storedRecipe, type FetchedRecipes } from './recipeView';

type Cell = string | number;

// preview: 저장 직후 새 탭에서도 연다(PDF). 팝업이 차단돼도 저장은 이미 끝났으므로 안내만 남긴다.
// 새 탭이 blob 을 다 읽기 전에 URL 이 사라지지 않도록 미리보기는 60초 뒤에 해제한다.
function saveBlob(name: string, blob: Blob, preview = false) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  if (preview && !window.open(url, '_blank')) {
    console.info(`[내려받기] '${name}' 은 저장됐지만 새 탭 자동 열림이 팝업 차단됐어요 — 주소창의 팝업 허용 후 다시 받거나 저장된 파일을 여세요.`);
  }
  setTimeout(() => URL.revokeObjectURL(url), preview ? 60_000 : 1000);
}

/** 내려받기 실패를 사람이 읽을 사유로 — 서버 미기동·HTTP 상태·백엔드 detail(reason/message)까지. */
export async function describeExportError(e: unknown): Promise<string> {
  const err = e as { response?: { status?: number; data?: unknown }; code?: string; message?: string };
  if (!err?.response) return err?.code === 'ECONNABORTED' ? '요청 시간 초과' : '백엔드 서버에 연결할 수 없음(8000 포트 확인)';
  let data = err.response.data;
  // PDF 요청은 responseType 'blob' 이라 에러 본문(JSON)도 Blob 으로 온다 → 텍스트로 풀어 detail 을 읽는다.
  if (data instanceof Blob) { try { data = JSON.parse(await data.text()); } catch { data = undefined; } }
  const detail = (data as { detail?: unknown } | undefined)?.detail;
  const why = typeof detail === 'string' ? detail
    : (detail as { message?: string; reason?: string } | undefined)?.message ?? (detail as { reason?: string } | undefined)?.reason
      ?? (Array.isArray(detail) ? '요청 형식 오류' : '');
  return `HTTP ${err.response.status}${why ? ` · ${why}` : ''}`;
}

export function saveCsv(name: string, rows: Cell[][]) {
  const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
  saveBlob(name, new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }));
}

// 식단표(CSV): 주차·요일·끼니별 메뉴 + 1인 열량·단백질. 첫 행이 머리행.
export function tableRows(plan: MealPlan): Cell[][] {
  const rows: Cell[][] = [['주차', '날짜', '요일', '끼니', '메뉴', '열량(kcal)', '단백질(g)']];
  plan.weeks.forEach((wk) => wk.days.forEach((d) => d.cells.forEach((c) => {
    rows.push([wk.label, d.date, d.dow, MEAL_TABLE[c.kind as MealKind], c.items.map((i) => i.name).join(' '), c.kcal, c.protein]);
  })));
  return rows;
}

// 식단표(PDF): 웹 식단표와 같은 그리드 — 행 = 하루, 열 = 실제 생성된 끼니. 값은 CSV 와 같은 칸(plan.weeks)에서 온다.
export function planGrid(plan: MealPlan): PdfExportRequest['grids'][number] {
  return {
    title: '식단표',
    corner: '날짜',
    columns: plan.meals.map((m) => MEAL_TABLE[m]),
    rows: plan.weeks.flatMap((wk) => wk.days.map((d) => ({
      label: `${d.date} (${d.dow})`,
      sub: wk.label,
      cells: plan.meals.map((m) => {
        const c = d.cells.find((x) => x.kind === m);
        return c ? { menus: c.items.map((i) => i.name), kcal: c.kcal, protein: c.protein } : null;
      }),
    }))),
  };
}

// 조리 지시서: 메뉴별 재료 투입량(총량)·조리 순서.
// menuRecipes(백엔드 recipe_ingredient_map 보강분)가 있으면 실데이터를, 없으면(mock/미보강 메뉴)
// 골격 문구로 저하한다.
export function recipeRows(weeksSrc: MealPlan['weeks'], headcount: number, menuRecipes?: MealPlan['menuRecipes'],
  menuRecipesById?: MealPlan['menuRecipesById']): Cell[][] {
  const rows: Cell[][] = [['날짜', '끼니', '메뉴', '재료명', '투입량(g, 총량)', '조리순서']];
  weeksSrc.forEach((wk) => wk.days.forEach((d) => d.cells.forEach((c) => {
    c.items.forEach((it) => {
      // 솔버가 고른 행(nutritionId)의 레시피를 먼저, 없으면 이름으로(대체식·구버전 저장본).
      const rec = (it.nutritionId != null ? menuRecipesById?.[String(it.nutritionId)] : undefined) ?? menuRecipes?.[it.name];
      if (rec && rec.ingredients.length) {
        rec.ingredients.forEach((ing) => rows.push([
          d.date, MEAL_TABLE[c.kind as MealKind], it.name,
          ing.name, ing.amount ?? '', ing.step ?? '',
        ]));
      } else {
        // 검토에서 교체한 메뉴는 생성 응답에 레시피가 없다 — 사실대로 표기(재생성하면 반영).
        const note = rec?.note || (it.orig ? '교체한 메뉴 — 레시피는 식단 재생성 후 반영' : '(재료 연동 예정)');
        rows.push([d.date, MEAL_TABLE[c.kind as MealKind], it.name, note, `1인분×${headcount}`, '(조리 순서 연동 예정)']);
      }
    });
  })));
  return rows;
}

/** PDF 상단 요약 — 확정 화면 요약 카드·KPI 와 같은 값. */
export function planSummary(plan: MealPlan): { label: string; value: string }[] {
  const mealsText = plan.meals.map((m) => MEAL_TABLE[m]).join('·');
  const allergyN = plan.alternatives.reduce((s, t) => s + t.count, 0);
  return [
    { label: '대상', value: planTargetLabel(plan) },
    { label: '인원', value: `${plan.headcount.toLocaleString()}명` },
    { label: '기간 · 끼니', value: `${planDateRange(plan)} 평일 ${plan.totalDays}일 · ${mealsText}` },
    { label: '1인 원가', value: `${plan.costPerPerson.toLocaleString()}원/식 (예산 ${plan.budgetPerPerson.toLocaleString()}원)` },
    { label: '총 식재료비', value: `${plan.totalCost.toLocaleString()}원` },
    { label: '알레르기 그룹', value: `${plan.alternatives.length}그룹 · ${allergyN}명` },
  ];
}

// 조리 지시서 PDF 용 메뉴별 레시피 — 레시피 화면(RecipeDrawer)과 같은 규칙: 응답·저장본에 있으면 그것,
// 없으면(교체한 메뉴·구버전 저장본) 서버 조회. 없는 칸은 비워 둔다(임의 생성 금지).
async function recipeSection(plan: MealPlan): Promise<PdfExportRequest['recipes']> {
  const entries = collectEntries(plan.weeks);
  const need = missingRecipes(plan, entries);
  let fetched: FetchedRecipes = { by_id: {}, by_name: {} };
  if (need.ids.length || need.names.length) fetched = await fetchMissingRecipes(need, plan.headcount);
  const fmt = (v: number) => Math.round(v * 10) / 10;
  return entries.map((e) => {
    const rec: MenuRecipe | undefined = storedRecipe(plan, e) ?? fetchedRecipe(fetched, e);
    return {
      name: e.name,
      meta: [rec?.cooking_method, e.uses.join(' · ')].filter(Boolean).join(' · '),
      ingredients: (rec?.ingredients ?? []).map((i) => [
        i.name, i.role ?? '', i.amount == null ? '분량 미기재' : fmt(i.amount), i.amount == null ? '' : fmt(i.amount / plan.headcount),
      ]),
      steps: rec?.steps ?? [],
    };
  });
}

/** PDF 요청 본문. withRecipes 면 일반식 조리 지시서(재료·원본 조리 순서)를 뒤에 붙인다. */
export async function buildPdfRequest(plan: MealPlan, name: string, withRecipes: boolean): Promise<PdfExportRequest> {
  return {
    title: name,
    file_name: `${name}_${withRecipes ? '식단표_조리지시서' : '식단표'}`,
    summary: planSummary(plan),
    grids: [planGrid(plan)],
    tables: [],
    recipes_title: '일반식 조리 지시서',
    recipes_note: withRecipes ? `투입량은 ${plan.headcount.toLocaleString()}명 기준 총량(g). 조리 순서는 데이터셋 원문이며, 없는 메뉴는 비워 둡니다.` : '',
    recipes: withRecipes ? await recipeSection(plan) : [],
  };
}

/** PDF 를 만들어 내려받는다. 실패는 호출부가 처리(reject). */
export async function downloadPlanPdf(plan: MealPlan, name: string, withRecipes: boolean): Promise<void> {
  const req = await buildPdfRequest(plan, name, withRecipes);
  const blob = await exportPlanPdf(req);
  saveBlob(`${req.file_name}.pdf`, blob, true);
}
