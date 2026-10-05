// src/pages/plan/Step2Review.tsx
// 식단 생성 2단계 · 검토 (시안 화면 5 / 5-a 3끼 / 5-b 대체식 / 5-c 교체 팝오버)
import { useEffect, useMemo, useState } from 'react';
import { Card, Button, Popover, Progress, App, Spin, Tooltip } from 'antd';
import { PrinterOutlined, CloseOutlined, CheckOutlined } from '@ant-design/icons';
import StepIndicator from './StepIndicator';
import KpiRow from '../../components/KpiRow';
import BudgetSummaryCard from './BudgetSummaryCard';
import {
  MEAL_TABLE, MEAL_TIME, CARRYOVER_BAND, planDateRange, planTargetLabel, fetchSwapCandidates, candidateNutri,
  checkSwap, budgetModeOf, planPeriodCost,
  type MealPlan, type MealItem, type MealKind, type MealCell,
  type SwapCandidate, type SwapCandidatesResponse, type SwapCheck, type TotalBudgetQuery,
} from '../../api/menu';
import { editPlan, altReviewText, ALT_REVIEW_LABEL, ALLERGY_CHECK_LABEL, type MainEdit } from './altSync';
import { changedNames } from './recipeView';

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7', line: '#EEF2F0', head: '#F7FAF8',
  green: '#12A150', greenText: '#0B6B36', tint: '#E4F7EB', tintBg: '#F1FAF4',
  red: '#E5484D', redText: '#B42318', redTint: '#FEF6F6', redBorder: '#F8D0D1', blue: '#2F6FED',
  // 끼니 원가 기준 이탈(참고) — 이월 모드에서 허용된 상태라 경고색(빨강)·배경 강조를 쓰지 않는다. 회청색 글자만.
  slate: '#5B6B7F', slateTint: '#EEF1F5', slateBorder: '#C9D1DC',
};

const noop = () => {};

const won = (n: number | null | undefined) => (n == null ? '–' : `${Math.round(n).toLocaleString()}원`);
const candLine = (c: { kcal: number; cost: number | null; sodium: number | null }) =>
  `${Math.round(c.kcal)} kcal · ${won(c.cost)} · ${c.sodium == null ? '–' : `${Math.round(c.sodium)}mg`}`;

type PanelState = { status: 'loading' } | { status: 'error' } | { status: 'ok'; data: SwapCandidatesResponse };

const redTag = (text: string) => (
  <span key={text} style={{ fontSize: 10, fontWeight: 600, color: C.redText, background: '#FADCDC', borderRadius: 5, padding: '0 5px', whiteSpace: 'nowrap' }}>{text}</span>
);

// 교체 팝오버 내용 — 열릴 때 마운트되어 같은 자리의 실후보를 불러온다(GET /api/menu/candidates).
// 렌더마다 새로 만들어지지 않도록 컴포넌트 밖에 둔다(상태 유지).
// check: 후보로 바꿨을 때 셀(한 끼)이 예산·나트륨 상한을 넘는지 — 넘으면 빨간 태그 + 교체 버튼 비활성.
// 되돌리기(원래 메뉴 = 솔버가 고른 메뉴)는 막지 않는다.
// total: carryover 모드면 교체 후 기간 총액 판정용 값(현재 총원가·총예산)을 서버에 넘긴다.
// allergens: 대체식 칸이면 그 그룹의 알레르겐(서버가 후보에서 거른다). 일반식 칸은 빈 배열.
function SwapPanel({ item, excludeIds, allergens, check, total, onPick, onRevert }: {
  item: MealItem; excludeIds: number[]; allergens: string[]; check: (c: SwapCandidate) => SwapCheck; total: TotalBudgetQuery | null;
  onPick: (c: SwapCandidate) => void; onRevert: () => void;
}) {
  const id = item.nutritionId;
  const [state, setState] = useState<PanelState>({ status: 'loading' });
  // 원래 메뉴(되돌리기 칸에 따로 보임)는 후보 목록에서 뺀다 — 같은 메뉴가 두 번 뜨지 않게.
  const excludeKey = [...excludeIds, ...(item.origNutritionId != null ? [item.origNutritionId] : [])].join(',');
  const allergenKey = allergens.join(',');
  const planTotal = total?.planTotalCost, totalBudget = total?.totalBudget;
  const mealCost = total?.mealCost, guardMin = total?.guardMin, guardMax = total?.guardMax;

  useEffect(() => {
    if (id == null) return;
    let alive = true;
    // 막히는 후보가 섞여도 고를 수 있는 후보가 남도록 넉넉히 받는다.
    const q = planTotal != null && totalBudget != null ? { planTotalCost: planTotal, totalBudget, mealCost, guardMin, guardMax } : null;
    fetchSwapCandidates(id, excludeKey ? excludeKey.split(',').map(Number) : [], 12, q, allergenKey ? allergenKey.split(',') : [])
      .then((data) => { if (alive) setState({ status: 'ok', data }); })
      .catch((e) => { console.error('[메뉴교체] 후보 조회 실패:', e); if (alive) setState({ status: 'error' }); });
    return () => { alive = false; };
  }, [id, excludeKey, allergenKey, planTotal, totalBudget, mealCost, guardMin, guardMax]);

  const canRevert = !!item.orig && item.orig !== item.name;
  const revertBox = canRevert && (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, border: `1px solid ${C.tint}`, background: C.tintBg, borderRadius: 10, padding: '9px 11px' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, color: C.text }}>{item.orig}</div>
        <div style={{ fontSize: 12, color: C.sub }}>원래 메뉴로 되돌리기{item.origNutri ? ` · ${candLine(item.origNutri)}` : ''}</div>
      </div>
      <Button size="small" onClick={onRevert}>되돌리기</Button>
    </div>
  );

  let body: React.ReactNode;
  if (id == null) {
    body = <div style={{ fontSize: 12, color: C.sub }}>이 칸은 교체 후보를 불러올 수 없어요 (대체식 칸이거나 이전 버전 식단)</div>;
  } else if (state.status === 'loading') {
    body = <div style={{ display: 'flex', justifyContent: 'center', padding: 16 }}><Spin size="small" /></div>;
  } else if (state.status === 'error') {
    body = <div style={{ fontSize: 12, color: C.redText }}>후보를 불러오지 못했어요. 잠시 후 다시 열어 주세요.</div>;
  } else if (!state.data.candidates.length) {
    body = <div style={{ fontSize: 12, color: C.sub }}>같은 자리({state.data.category})에 바꿀 수 있는 다른 메뉴가 없어요</div>;
  } else {
    // 교체 가능한 후보를 위로(각 그룹 안에서는 서버 순서 = 열량 가까운 순 유지).
    const rows = state.data.candidates.map((c) => ({ c, k: check(c) }))
      .sort((a, b) => Number(b.k.allowed) - Number(a.k.allowed));
    body = rows.map(({ c, k }) => (
      <div key={c.menu_id} style={{ display: 'flex', alignItems: 'center', gap: 10, border: `1px solid ${k.allowed ? C.line : C.redBorder}`, background: k.allowed ? '#fff' : C.redTint, borderRadius: 10, padding: '9px 11px' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: k.allowed ? C.text : C.sub }}>{c.name}</span>
            {k.overBudget && redTag(k.mode === 'carryover' ? '기간 총예산 초과' : '예산 초과')}
            {k.overGuard && redTag(k.guardMax != null && k.costAfter > k.guardMax
              ? `끼니 원가 범위 초과(최대 ${won(k.guardMax)})` : `끼니 원가 범위 미달(최소 ${won(k.guardMin)})`)}
            {k.overSodium && k.sodiumCap != null && redTag(`하루 나트륨 ${Math.round(k.sodiumAfter ?? 0).toLocaleString()}/${Math.round(k.sodiumCap).toLocaleString()}mg`)}
            {k.sodiumUnknown && redTag('나트륨 정보 없음')}
          </div>
          <div style={{ fontSize: 12, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>{candLine(c)}</div>
          {k.allowed && k.overSodium && (
            <div style={{ fontSize: 11, color: C.redText }}>교체할 수 있지만 그날 나트륨이 하루 상한을 넘어 경고가 표시돼요</div>
          )}
          {k.allowed && k.mode === 'carryover' && k.periodAfter != null && k.totalBudget != null && (
            <div style={{ fontSize: 11, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>
              교체 후 기간 총액 {won(k.periodAfter)} · 여유 {won(k.totalBudget - k.periodAfter)}
            </div>
          )}
          {!k.allowed && (
            <div style={{ fontSize: 11, color: C.redText, fontVariantNumeric: 'tabular-nums' }}>
              {k.mode === 'carryover'
                ? [k.overBudget ? `교체 시 기간 총액 ${won(k.periodAfter)} > 총예산 ${won(k.totalBudget)}` : '',
                  k.overGuard ? `교체 시 끼니 원가 ${won(k.costAfter)} — 범위 ${won(k.guardMin)}~${won(k.guardMax)} 밖` : '']
                  .filter(Boolean).join(' · ')
                : `교체 시 한 끼${k.overBudget ? ` 원가 ${won(k.costAfter)} > 예산 ${won(k.budget)}` : ''}`}
              {k.sodiumUnknown ? ' 나트륨을 알 수 없어 상한 확인 불가' : ''}
            </div>
          )}
        </div>
        <Button size="small" type="primary" disabled={!k.allowed} onClick={() => onPick(c)}>교체</Button>
      </div>
    ));
  }

  return (
    <div style={{ width: 320 }}>
      {state.status === 'ok' && (
        <div style={{ fontSize: 12, color: C.sub, marginBottom: 8 }}>
          같은 자리({state.data.category}) 실메뉴 · 열량 가까운 순 · 지금 {candLine(state.data.current)}
        </div>
      )}
      {/* 후보가 많아도 창이 화면 밖으로 넘치지 않도록 높이를 고정하고 안에서 스크롤 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7, maxHeight: 360, overflowY: 'auto', paddingRight: 2 }}>
        {revertBox}
        {body}
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: C.muted }}>
        {total ? `기간 총예산·끼니 원가 범위${total.guardMax != null ? `(${won(total.guardMin)}~${won(total.guardMax)})` : ''}를 넘는 후보는 고를 수 없어요(범위 안에서 한 끼가 1식 예산을 넘는 건 이월로 허용)`
          : '한 끼 예산을 넘는 후보는 고를 수 없어요'}. 하루 나트륨 상한을 넘기는 교체는 가능하지만 그날에 경고가 떠요. 3일 중복·반상 구성 같은 나머지 제약의 재검증은 식단 재생성에서 반영돼요
      </div>
    </div>
  );
}

// readOnly: 저장된 식단 열람(/plans/:id)용. 교체·삭제·조건 수정·체크 토글·이전/다음을 숨기고
// 일반식/대체식 보기 전환만 남긴다. 편집 콜백은 readOnly 일 때 생략할 수 있다.
export default function Step2Review({ plan, setPlan = noop, onPrev = noop, onNext = noop, onEditConditions = noop, readOnly = false }: {
  plan: MealPlan; setPlan?: (p: MealPlan) => void; onPrev?: () => void; onNext?: () => void; onEditConditions?: () => void;
  readOnly?: boolean;
}) {
  const { message } = App.useApp();
  const [view, setView] = useState<'normal' | 'alt'>('normal');
  const [track, setTrack] = useState(0);

  const weeks = useMemo(
    () => (view === 'alt' && plan.alternatives.length ? plan.alternatives[track].weeks : plan.weeks),
    [view, track, plan],
  );
  const doneChecks = plan.checks.filter((c) => c.done).length;
  const carryover = budgetModeOf(plan) === 'carryover';
  // carryover 교체 판정에 넘길 현재 기간 총원가·총예산(교체·삭제 뒤 plan 이 바뀌면 다시 계산).
  const totalQuery: TotalBudgetQuery | null = useMemo(
    () => (carryover && plan.budgetTotal != null ? { planTotalCost: planPeriodCost(plan), totalBudget: plan.budgetTotal } : null),
    [carryover, plan],
  );
  const band = { lo: Math.round(plan.budgetPerPerson * CARRYOVER_BAND.low), hi: Math.round(plan.budgetPerPerson * CARRYOVER_BAND.high) };

  // 교체 후보 제외 목록: 지금 보고 있는 식단(본식단 또는 선택한 대체식 트랙)에 올라 있는 메뉴 id
  // (같은 메뉴를 한 식단에 두 번 넣지 않게). 대체식 트랙이면 그 그룹 알레르겐도 후보에서 거른다.
  const planIds = useMemo(
    () => [...new Set(weeks.flatMap((w) => w.days.flatMap((d) => d.cells.flatMap((c) => c.items.map((it) => it.nutritionId)))))]
      .filter((x): x is number => x != null),
    [weeks],
  );
  const trackAllergens = useMemo(
    () => (view === 'alt' ? plan.alternatives[track]?.allergens ?? [] : []),
    [view, track, plan.alternatives],
  );

  // 교체·되돌리기·삭제는 칸 식별자(menuId)로 찾고 셀·총합·달성률·원가를 다시 계산한다(altSync.editPlan).
  // 일반식 메뉴 편집은 대체식 트랙 같은 칸에도 반영 — 대체되지 않은 자리는 따라가고, 알레르기 대체 자리는 재검토 표시.
  //   대체식 탭에서 대체식 메뉴를 직접 고치면 그 메뉴만 고친다.
  const edit = (it: MealItem, e: MainEdit) => setPlan(editPlan(plan, it, e));
  const onDelete = (it: MealItem) => {
    edit(it, { kind: 'delete' });
    message.success(`'${it.name}' 삭제`);
  };
  const onSwap = (it: MealItem, cell: MealCell, c: SwapCandidate) => {
    // 버튼 비활성과 같은 판정을 한 번 더 — 예산·나트륨 상한을 넘기는 교체는 적용하지 않는다.
    const k = checkSwap(plan, cell, it, c);
    if (!k.allowed) {
      message.warning(`'${c.name}'(으)로 바꾸면 ${carryover ? '기간 총예산·끼니 원가 범위' : '한 끼 예산'}를 넘거나 나트륨을 알 수 없어요`);
      return;
    }
    edit(it, { kind: 'swap', to: { name: c.name, nutritionId: c.menu_id, nutri: candidateNutri(c) } });
    if (k.overSodium) message.warning(`'${it.name}' → '${c.name}' 교체 — 그날 나트륨이 하루 상한을 넘어요`);
    else message.success(`'${it.name}' → '${c.name}' 교체`);
  };
  const onRevert = (it: MealItem) => {
    edit(it, { kind: 'revert' });
    message.success(`'${it.orig}'(으)로 되돌렸어요`);
  };
  const toggleCheck = (label: string) => {
    if (readOnly) return;
    setPlan({ ...plan, checks: plan.checks.map((c) => (c.label === label ? { ...c, done: !c.done } : c)) });
  };

  // '대체' 배지: 대체식 탭에서 일반식 같은 칸과 다른 메뉴(알레르기 대체)에만 — PDF·CSV 의 [대체] 와 같은 기준(changedNames).
  //   대체식 트랙 메뉴는 전부 alt=true 로 오므로 alt 만 보면 따라간 메뉴에도 배지가 붙었다.
  const MenuLine = ({ it, cell, changed }: { it: MealItem; cell: MealCell; changed?: Set<string> }) => {
    const isAlt = changed ? changed.has(it.name) : !!it.alt;
    if (readOnly) {
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, lineHeight: '20px' }}>
          <span style={{ fontSize: 13, color: it.flag ? C.redText : isAlt ? C.greenText : C.text }}>{it.name}</span>
          {isAlt && <span style={{ fontSize: 10, fontWeight: 600, color: C.greenText, background: '#D2F1DF', borderRadius: 5, padding: '0 5px' }}>대체</span>}
          {it.flag && <span style={{ fontSize: 10, fontWeight: 600, color: C.redText, background: '#FADCDC', borderRadius: 5, padding: '0 5px' }}>{it.flag}</span>}
          {it.allergyCheck && (
            <Tooltip title={`영양사 확인 필요 — ${it.allergyCheck}`}>
              <span style={{ fontSize: 10, fontWeight: 600, color: C.redText, background: '#FADCDC', borderRadius: 5, padding: '0 5px' }}>{ALLERGY_CHECK_LABEL}</span>
            </Tooltip>
          )}
        </div>
      );
    }
    return (
      <div className="menuline" style={{ display: 'flex', alignItems: 'center', gap: 5, lineHeight: '20px' }}>
        <Popover
          trigger="click" placement="bottom" destroyOnHidden
          title={<span style={{ fontSize: 13 }}>‘{it.name}’ 대신</span>}
          content={<SwapPanel item={it} excludeIds={planIds} allergens={trackAllergens} check={(c) => checkSwap(plan, cell, it, c)}
            total={totalQuery && { ...totalQuery, mealCost: cell.cost, guardMin: plan.guardMin, guardMax: plan.guardMax }}
            onPick={(c) => onSwap(it, cell, c)} onRevert={() => onRevert(it)} />}
        >
          <span style={{ fontSize: 13, color: it.flag ? C.redText : isAlt ? C.greenText : C.text, cursor: 'pointer' }}>{it.name}</span>
        </Popover>
        {isAlt && <span style={{ fontSize: 10, fontWeight: 600, color: C.greenText, background: '#D2F1DF', borderRadius: 5, padding: '0 5px' }}>대체</span>}
        {it.flag && <span style={{ fontSize: 10, fontWeight: 600, color: C.redText, background: '#FADCDC', borderRadius: 5, padding: '0 5px' }}>{it.flag}</span>}
        {it.allergyCheck && (
          <Tooltip title={`영양사 확인 필요 — ${it.allergyCheck}`}>
            <span style={{ fontSize: 10, fontWeight: 600, color: C.redText, background: '#FADCDC', borderRadius: 5, padding: '0 5px' }}>{ALLERGY_CHECK_LABEL}</span>
          </Tooltip>
        )}
        <span className="del" style={{ opacity: 0, cursor: 'pointer', color: C.muted, transition: 'opacity .1s' }} onClick={() => onDelete(it)}>
          <CloseOutlined style={{ fontSize: 10 }} />
        </span>
      </div>
    );
  };

  const Cell = ({ cell, changed }: { cell?: MealCell; changed?: Set<string> }) => {
    if (!cell) return <td style={{ border: `1px solid ${C.line}`, verticalAlign: 'top' }} />;
    // 나트륨 등 실제 위반만 빨간 배경(경고). 이월 밴드 밖(B×0.8~1.2) 원가는 **참고** — 배경·테두리 없이
    //   회청색 작은 글자 "1인 N원 ▲기준 초과 / ▼기준 미만" + 툴팁만(색만으로 구분하지 않도록 문구 포함).
    const bandTip = cell.band && cell.cost != null
      ? `참고: 이 끼니 1인 원가 ${Math.round(cell.cost).toLocaleString()}원 — 1식 예산의 ${CARRYOVER_BAND.low * 100}~${CARRYOVER_BAND.high * 100}%(${band.lo.toLocaleString()}~${band.hi.toLocaleString()}원)보다 ${cell.band === 'high' ? '높아요' : '낮아요'}. 기간 총예산·끼니 원가 범위 안이라 이월로 허용된 상태예요`
      : undefined;
    return (
      <td style={{ border: `1px solid ${C.line}`, verticalAlign: 'top', padding: '10px 12px', background: cell.warn ? C.redTint : '#fff', minWidth: 150 }}>
        {cell.altReview?.length ? (
          <Tooltip title={altReviewText(cell)}>
            <div style={{ marginBottom: 6, fontSize: 11, fontWeight: 600, color: '#8A5A00', background: '#FBF2DF', borderRadius: 5, padding: '1px 6px', display: 'inline-block' }}>{ALT_REVIEW_LABEL}</div>
          </Tooltip>
        ) : null}
        {cell.items.map((it) => <MenuLine key={it.menuId ?? it.name} it={it} cell={cell} changed={changed} />)}
        <div style={{ marginTop: 8, fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
          <span style={{ fontWeight: 700, color: cell.warn ? C.redText : C.text }}>{cell.kcal} kcal</span>
          <span style={{ color: C.sub, marginLeft: 12 }}>단백 {cell.protein.toFixed(1)}g</span>
        </div>
        {cell.cost != null && (
          <Tooltip title={bandTip}>
            <div style={{ marginTop: 2, fontSize: 11, fontVariantNumeric: 'tabular-nums', color: cell.band ? C.slate : C.muted }}>
              1인 {Math.round(cell.cost).toLocaleString()}원{cell.band ? (cell.band === 'high' ? ' ▲기준 초과' : ' ▼기준 미만') : ''}
            </div>
          </Tooltip>
        )}
      </td>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 스텝 + 토글 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        {!readOnly && <StepIndicator current={2} />}
        <div style={{ flex: 1 }} />
        {view === 'alt' && plan.alternatives.map((t, i) => (
          <span key={i} onClick={() => setTrack(i)} style={{ fontSize: 12, fontWeight: 600, cursor: 'pointer', borderRadius: 8, padding: '5px 11px', color: track === i ? C.greenText : C.sub, background: track === i ? C.tint : '#fff', border: `1px solid ${track === i ? C.tint : C.border}` }}>{t.label} {t.count}명</span>
        ))}
        <div style={{ display: 'flex', background: '#EEF2F0', borderRadius: 10, padding: 3 }}>
          {(['normal', 'alt'] as const).map((v) => (
            <div key={v} onClick={() => v === 'normal' || plan.alternatives.length ? setView(v) : null}
              style={{ height: 30, padding: '0 14px', borderRadius: 8, display: 'flex', alignItems: 'center', fontSize: 13, fontWeight: view === v ? 600 : 400, cursor: 'pointer', background: view === v ? '#fff' : 'transparent', color: view === v ? C.text : C.sub, opacity: v === 'alt' && !plan.alternatives.length ? 0.4 : 1 }}>
              {v === 'normal' ? '일반식' : '대체식'}
            </div>
          ))}
        </div>
      </div>

      {/* 조건 요약 바 */}
      <Card size="small">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 14, color: C.text, fontVariantNumeric: 'tabular-nums' }}>{plan.conditionText}</span>
          <div style={{ flex: 1 }} />
          {!readOnly && <Button size="small" onClick={onEditConditions}>조건 수정</Button>}
        </div>
        {/* 생성 근거 — 솔버 응답(hard_breakdown)에서 실제 충족된 제약만 담긴다(toMealPlan). */}
        {plan.rationale?.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            <span style={{ fontSize: 12, color: C.sub, marginRight: 2 }}>생성 근거</span>
            {plan.rationale.map((r) => (
              <span key={r} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 500, color: C.greenText, background: C.tintBg, border: `1px solid ${C.tint}`, borderRadius: 999, padding: '2px 10px', fontVariantNumeric: 'tabular-nums' }}>
                <CheckOutlined style={{ fontSize: 10, color: C.green }} />{r}
              </span>
            ))}
          </div>
        )}
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 316px', gap: 16, alignItems: 'start' }}>
        {/* 식단표 */}
        <Card size="small" styles={{ body: { padding: 20 } }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>주간 식단표</span>
            <div style={{ flex: 1 }} />
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: C.sub }}>
                <span style={{ width: 9, height: 9, borderRadius: 3, background: C.redTint, border: `1px solid ${C.redBorder}` }} />
                {carryover ? '나트륨 하루 상한 초과(경고)' : '끼니 예산·나트륨 하루 상한 초과(경고)'}
              </span>
              {carryover && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: C.sub }}>
                  <span style={{ fontSize: 11, color: C.slate, fontVariantNumeric: 'tabular-nums' }}>▲▼</span>
                  끼니 원가 기준 이탈(참고, 이월 허용 범위 · 기준 {band.lo.toLocaleString()}~{band.hi.toLocaleString()}원)
                </span>
              )}
            </div>
            <Button size="small" icon={<PrinterOutlined />} onClick={() => window.print()}>인쇄</Button>
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: C.sub }}>{planTargetLabel(plan)} · {planDateRange(plan)} · {plan.headcount}명 · 단위 1인 기준</div>

          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 14, tableLayout: 'fixed' }}>
            {weeks.map((wk, wi) => (
              <tbody key={wk.label}>
                <tr>
                  <th style={{ width: 92, border: `1px solid ${C.line}`, background: C.head, padding: '9px 12px', textAlign: 'left', fontSize: 13, color: C.text }}>{wk.label}</th>
                  {wk.days.map((d) => (
                    <th key={d.date} style={{ border: `1px solid ${d.sodiumOver ? C.redBorder : C.line}`, background: d.sodiumOver ? C.redTint : C.head, padding: '7px 12px', textAlign: 'center', fontWeight: 600 }}>
                      <div style={{ fontSize: 13, color: d.sodiumOver ? C.redText : C.text }}>{d.dow}</div>
                      <div style={{ fontSize: 11, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>{d.date}</div>
                      {d.sodiumOver && plan.sodiumCapPerDay != null && (
                        <Tooltip title={`이날 1인 나트륨 합계 ${Math.round(d.sodium ?? 0).toLocaleString()}mg — 하루 상한 ${Math.round(plan.sodiumCapPerDay).toLocaleString()}mg 초과(경고)`}>
                          <div style={{ marginTop: 2, fontSize: 10, fontWeight: 600, color: C.redText, fontVariantNumeric: 'tabular-nums' }}>
                            나트륨 {Math.round(d.sodium ?? 0).toLocaleString()}/{Math.round(plan.sodiumCapPerDay).toLocaleString()}mg
                          </div>
                        </Tooltip>
                      )}
                    </th>
                  ))}
                </tr>
                {plan.meals.map((meal) => (
                  <tr key={meal}>
                    <td style={{ border: `1px solid ${C.line}`, background: C.head, padding: '10px 12px', verticalAlign: 'top' }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: C.green }}>{MEAL_TABLE[meal as MealKind]}</div>
                      <div style={{ fontSize: 11, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>{MEAL_TIME[meal as MealKind]}</div>
                    </td>
                    {wk.days.map((d, di) => <Cell key={d.date + meal} cell={d.cells.find((c) => c.kind === meal)}
                      changed={view === 'alt' && plan.alternatives.length ? changedNames(plan.weeks, weeks, wi, di, meal as MealKind) : undefined} />)}
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
          <div style={{ marginTop: 12, fontSize: 12, color: C.muted }}>
            {readOnly ? '저장된 식단(읽기 전용)' : '메뉴에 마우스를 올리면 교체·삭제할 수 있어요'} · 1인 기준 열량(kcal)과 단백질(g)
          </div>
        </Card>

        {/* 사이드바 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <KpiRow variant="bar" achievement={plan.achievement} cost={{ value: plan.costPerPerson, budget: plan.budgetPerPerson }} />
          <BudgetSummaryCard plan={plan} />

          <Card size="small">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>확인 필요</span>
              <div style={{ flex: 1 }} />
              <span style={{ fontSize: 12, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>{doneChecks}/{plan.checks.length}</span>
            </div>
            <Progress percent={Math.round((doneChecks / plan.checks.length) * 100)} showInfo={false} strokeColor={C.green} style={{ marginTop: 8 }} />
            <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column' }}>
              {plan.checks.map((c) => (
                <div key={c.label} onClick={() => toggleCheck(c.label)} style={{ display: 'flex', alignItems: 'center', gap: 11, height: 40, cursor: readOnly ? 'default' : 'pointer' }}>
                  <span style={{ width: 17, height: 17, borderRadius: 5, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', background: c.done ? C.green : '#fff', border: c.done ? 'none' : `1px solid #C4CEC9` }}>
                    {c.done && <CheckOutlined style={{ fontSize: 10, color: '#fff' }} />}
                  </span>
                  <span style={{ flex: 1, fontSize: 13, color: c.done ? C.muted : C.text, textDecoration: c.done ? 'line-through' : 'none' }}>{c.label}</span>
                  {c.view && <span style={{ fontSize: 12, color: C.green, cursor: 'pointer' }} onClick={(e) => { e.stopPropagation(); setView('alt'); }}>보기 →</span>}
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      {!readOnly && (
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <Button onClick={onPrev}>이전</Button>
          <div style={{ flex: 1 }} />
          <Button type="primary" onClick={onNext}>다음: 확정</Button>
        </div>
      )}

      <style>{`.menuline:hover .del{opacity:1 !important}`}</style>
    </div>
  );
}