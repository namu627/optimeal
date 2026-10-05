// src/pages/plan/planExport.ts
// 확정 식단 내려받기 — CSV(식단표·조리 지시서)와 PDF. 식단표 행은 CSV·PDF 가 같은 함수(tableRows)를 쓴다.
// PDF 조판은 백엔드(/api/menu/export/pdf, 한글 폰트 임베드)가 하고, 내용은 여기서 만든 행 그대로다.
import {
  MEAL_TABLE, budgetSummary, exportPlanPdf, planDateRange, planTargetLabel,
  type MealPlan, type MealKind, type MealCell, type PdfExportRequest, type PdfRecipe, type WeekBlock,
} from '../../api/menu';
import {
  amountBasis, changedAltTracks, changedNames, collectEntries, perServing, resolveRecipes, type RecipeEntry,
} from './recipeView';
import { allergyCheckText, altFollowText, altReviewText } from './altSync';

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

// 조리 지시서(CSV): 메뉴별 재료 투입량(총량)·조리 순서. 레시피는 PDF 조리 지시서·레시피 화면과 같은 규칙으로 찾는다 —
// 응답·저장본에 있으면 그것, 없으면(교체한 메뉴·대체식·구버전 저장본) 서버 조회(fetchMissingRecipes).
// 조회해도 없으면 재료명 칸에 '레시피 미등록', 투입량·조리순서는 빈칸(임의 생성 금지).
export const RECIPE_MISSING = '레시피 미등록';
// 투입량 옆 '기준' 칸: 스케일링(업장 보정) | 단순 비례(1인분×인원) — recipeView.amountBasis.
const RECIPE_HEADER = ['날짜', '끼니', '메뉴', '재료명', '투입량(g, 총량)', '기준', '조리순서'];

function pushRecipeRows(rows: Cell[][], prefix: Cell[], weeksSrc: WeekBlock[], entries: RecipeEntry[],
  recipeOf: Awaited<ReturnType<typeof resolveRecipes>>, suffix?: (c: MealCell) => Cell[]) {
  const byKey = new Map(entries.map((e) => [e.key, e]));
  weeksSrc.forEach((wk) => wk.days.forEach((d) => d.cells.forEach((c) => {
    c.items.forEach((it) => {
      const e = byKey.get(it.nutritionId != null ? `id:${it.nutritionId}` : `name:${it.name}`)!;
      const rec = recipeOf(e);
      const meal = MEAL_TABLE[c.kind as MealKind];
      if (rec && rec.ingredients.length) {
        rec.ingredients.forEach((ing) => rows.push([...prefix, d.date, meal, it.name, ing.name, ing.amount ?? '', amountBasis(ing), ing.step ?? '', ...(suffix?.(c) ?? [])]));
      } else {
        rows.push([...prefix, d.date, meal, it.name, RECIPE_MISSING, '', '', '', ...(suffix?.(c) ?? [])]);
      }
    });
  })));
}

export async function recipeRows(plan: MealPlan, weeksSrc: WeekBlock[]): Promise<Cell[][]> {
  const entries = collectEntries(weeksSrc);
  const recipeOf = await resolveRecipes(plan, entries);
  const rows: Cell[][] = [RECIPE_HEADER];
  pushRecipeRows(rows, [], weeksSrc, entries, recipeOf);
  return rows;
}

// 대체식 조리 지시서(CSV): 그룹마다 일반식과 달라진 메뉴만(changedAltTracks) — PDF 대체식 조리 지시서와 같은 메뉴 집합.
export async function altRecipeRows(plan: MealPlan): Promise<Cell[][]> {
  const tracks = changedAltTracks(plan);
  const entries = collectEntries(tracks.flatMap((t) => t.weeks));
  const recipeOf = await resolveRecipes(plan, entries);
  // 비고: 일반식 교체·삭제가 알레르기 대체 자리에 걸린 칸이면 '대체식 재검토 필요(원인)'(altSync).
  const rows: Cell[][] = [['그룹', ...RECIPE_HEADER, '비고']];
  tracks.forEach((t) => pushRecipeRows(rows, [t.label], t.weeks, entries, recipeOf,
    (c) => [[allergyCheckText(c), altReviewText(c)].filter(Boolean).join(' / ')]));
  return rows;
}

/** PDF 상단 요약 — 확정 화면 요약 카드·KPI 와 같은 값. 이월 모드면 총액 요약 카드(BudgetSummaryCard)의 값도 싣는다. */
export function planSummary(plan: MealPlan): { label: string; value: string }[] {
  const mealsText = plan.meals.map((m) => MEAL_TABLE[m]).join('·');
  const allergyN = plan.alternatives.reduce((s, t) => s + t.count, 0);
  const won = (n: number) => `${n.toLocaleString()}원`;
  const out = [
    { label: '대상', value: planTargetLabel(plan) },
    { label: '인원', value: `${plan.headcount.toLocaleString()}명` },
    { label: '기간 · 끼니', value: `${planDateRange(plan)} 평일 ${plan.totalDays}일 · ${mealsText}` },
    { label: '1인 원가', value: `${plan.costPerPerson.toLocaleString()}원/식 (예산 ${plan.budgetPerPerson.toLocaleString()}원)` },
    { label: '총 식재료비', value: won(plan.totalCost) },
    { label: '알레르기 그룹', value: `${plan.alternatives.length}그룹 · ${allergyN}명` },
  ];
  const s = budgetSummary(plan);
  if (s) {
    out.push(
      { label: '기간 총원가', value: `${won(s.total)} (1인)` },
      { label: '총예산', value: `${won(s.budget)} (1인)` },
      { label: s.headroom < 0 ? '초과' : '여유', value: won(Math.abs(s.headroom)) },
      { label: '끼니 평균', value: won(s.mealAvg) },
      { label: '끼니 원가 범위', value: s.guardMin != null && s.guardMax != null
        ? `${s.guardMin.toLocaleString()}~${won(s.guardMax)}`
        : s.guardRelaxed ? '범위 제한 없이 생성(울타리 해제)' : '—' },
    );
  }
  return out;
}

/** 알레르기 그룹별 대체 메뉴 표 — 일반식과 달라진 끼니 + 검토에서 일반식을 고친 끼니(따라감·재검토 필요).
 *  바뀐 메뉴 앞에 '[대체]'. 따라간 끼니는 대체식이 일반식과 같지만 교체가 반영됐음을 '※ 일반식 교체 반영(…)'으로 적는다.
 *  PDF 표는 10개까지라 그룹도 10개까지. */
export const ALT_MARK = '[대체] ';
export function altTables(plan: MealPlan): PdfExportRequest['tables'] {
  return plan.alternatives.slice(0, 10).map((t) => {
    const rows: (string | number | null)[][] = [];
    let substituted = 0, followed = 0;
    t.weeks.forEach((wk, w) => wk.days.forEach((d, di) => d.cells.forEach((c) => {
      const changed = changedNames(plan.weeks, t.weeks, w, di, c.kind);
      const follow = altFollowText(c);
      const notes = [allergyCheckText(c), altReviewText(c), follow].filter(Boolean);
      if (!changed.size && !notes.length) return;
      if (changed.size) substituted += 1;
      if (follow) followed += 1;
      const main = plan.weeks[w]?.days[di]?.cells.find((x) => x.kind === c.kind);
      rows.push([`${d.date} (${d.dow})`, MEAL_TABLE[c.kind as MealKind],
        (main?.items ?? []).map((i) => i.name).join(', '),
        c.items.map((i) => (changed.has(i.name) ? ALT_MARK : '') + i.name).join(', ') + notes.map((n) => ` ※ ${n}`).join('')]);
    })));
    return {
      title: `대체식 · ${t.label} (${t.count}명) — 바뀐 끼니 ${substituted}개${followed ? ` · 일반식 교체 반영 ${followed}개` : ''}`,
      header: ['날짜', '끼니', '일반식', '대체식'], rows,
      empty_text: '일반식과 같아요 — 이 그룹의 알레르기 때문에 바뀐 끼니가 없어요',
    };
  });
}

// 조리 지시서 PDF 용 메뉴별 레시피 — 레시피 화면(RecipeDrawer)과 같은 규칙: 응답·저장본에 있으면 그것,
// 없으면(교체한 메뉴·구버전 저장본) 서버 조회. 없는 칸은 비워 둔다(임의 생성 금지).
// weeksSrc 는 일반식이면 plan.weeks, 대체식이면 changedAltTracks 의 weeks — 대체식 조리 지시서 CSV(altRecipeRows)와 같은 메뉴 집합.
async function recipeSection(plan: MealPlan, weeksSrc: WeekBlock[]): Promise<PdfRecipe[]> {
  const entries = collectEntries(weeksSrc);
  const recipeOf = await resolveRecipes(plan, entries);
  const fmt = (v: number) => Math.round(v * 10) / 10;
  return entries.map((e) => {
    const rec = recipeOf(e);
    return {
      name: e.name,
      meta: [rec?.cooking_method, e.uses.join(' · ')].filter(Boolean).join(' · '),
      ingredients: (rec?.ingredients ?? []).map((i) => {
        const per = perServing(i, plan.headcount);
        return [i.name, i.role ?? '', i.amount == null ? '분량 미기재' : fmt(i.amount), per == null ? '' : fmt(per), amountBasis(i)];
      }),
      steps: rec?.steps ?? [],
    };
  });
}

export interface PdfOptions {
  /** 일반식 조리 지시서 */
  recipes: boolean;
  /** 알레르기 그룹별 대체 메뉴 표(+ recipes 면 대체식 조리 지시서). 그룹이 없으면 무시. */
  alternatives: boolean;
}

const recipeNote = (plan: MealPlan) => `투입량은 ${plan.headcount.toLocaleString()}명 기준 총량(g). 기준 칸: 스케일링 = 업장 보정값, `
  + '단순 비례 = 1인분×인원. 조리 순서는 데이터셋 원문이며, 없는 메뉴는 비워 둡니다.';

/** PDF 요청 본문. 식단표 뒤에 (대체 메뉴 표) → (일반식 조리 지시서) → (대체식 조리 지시서) 순. */
export async function buildPdfRequest(plan: MealPlan, name: string, opts: PdfOptions): Promise<PdfExportRequest> {
  const withAlt = opts.alternatives && plan.alternatives.length > 0;
  const altWeeks = changedAltTracks(plan).flatMap((t) => t.weeks);
  const [recipes, altRecipes] = await Promise.all([
    opts.recipes ? recipeSection(plan, plan.weeks) : Promise.resolve([]),
    opts.recipes && withAlt ? recipeSection(plan, altWeeks) : Promise.resolve([]),
  ]);
  const parts = ['식단표', withAlt && '대체식', opts.recipes && '조리지시서'].filter(Boolean).join('_');
  return {
    title: name,
    file_name: `${name}_${parts}`,
    summary: planSummary(plan),
    grids: [planGrid(plan)],
    tables: withAlt ? altTables(plan) : [],
    recipes_title: '일반식 조리 지시서',
    recipes_note: opts.recipes ? recipeNote(plan) : '',
    recipes,
    recipe_sections: altRecipes.length ? [{
      title: '대체식 조리 지시서',
      note: `알레르기 그룹 대체식에서 일반식과 달라진 메뉴 ${altRecipes.length}개(대체식 조리 지시서 CSV와 같은 메뉴). ` + recipeNote(plan),
      recipes: altRecipes,
    }] : [],
  };
}

/** PDF 를 만들어 내려받는다. 실패는 호출부가 처리(reject). */
export async function downloadPlanPdf(plan: MealPlan, name: string, opts: PdfOptions): Promise<void> {
  const req = await buildPdfRequest(plan, name, opts);
  const blob = await exportPlanPdf(req);
  saveBlob(`${req.file_name}.pdf`, blob, true);
}
