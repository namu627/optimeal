// src/pages/plan/Step2Review.tsx
// 식단 생성 2단계 · 검토 (시안 화면 5 / 5-a 3끼 / 5-b 대체식 / 5-c 교체 팝오버)
import { useEffect, useMemo, useState } from 'react';
import { Card, Button, Popover, Progress, App, Spin } from 'antd';
import { PrinterOutlined, CloseOutlined, CheckOutlined } from '@ant-design/icons';
import StepIndicator from './StepIndicator';
import KpiRow from '../../components/KpiRow';
import {
  MEAL_TABLE, MEAL_TIME, planDateRange, planTargetLabel, fetchSwapCandidates, recomputePlan, candidateNutri, checkSwap,
  type MealPlan, type WeekBlock, type MealItem, type MealKind, type MealCell,
  type SwapCandidate, type SwapCandidatesResponse, type SwapCheck,
} from '../../api/menu';

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7', line: '#EEF2F0', head: '#F7FAF8',
  green: '#12A150', greenText: '#0B6B36', tint: '#E4F7EB', tintBg: '#F1FAF4',
  red: '#E5484D', redText: '#B42318', redTint: '#FEF6F6', redBorder: '#F8D0D1', blue: '#2F6FED',
};

function editItems(plan: MealPlan, fn: (items: MealItem[]) => MealItem[]): MealPlan {
  const mapW = (weeks: WeekBlock[]): WeekBlock[] =>
    weeks.map((w) => ({ ...w, days: w.days.map((d) => ({ ...d, cells: d.cells.map((c) => ({ ...c, items: fn(c.items) })) })) }));
  return { ...plan, weeks: mapW(plan.weeks), alternatives: plan.alternatives.map((t) => ({ ...t, weeks: mapW(t.weeks) })) };
}

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
function SwapPanel({ item, excludeIds, check, onPick, onRevert }: {
  item: MealItem; excludeIds: number[]; check: (c: SwapCandidate) => SwapCheck;
  onPick: (c: SwapCandidate) => void; onRevert: () => void;
}) {
  const id = item.nutritionId;
  const [state, setState] = useState<PanelState>({ status: 'loading' });
  const excludeKey = excludeIds.join(',');

  useEffect(() => {
    if (id == null) return;
    let alive = true;
    // 막히는 후보가 섞여도 고를 수 있는 후보가 남도록 넉넉히 받는다.
    fetchSwapCandidates(id, excludeKey ? excludeKey.split(',').map(Number) : [], 12)
      .then((data) => { if (alive) setState({ status: 'ok', data }); })
      .catch((e) => { console.error('[메뉴교체] 후보 조회 실패:', e); if (alive) setState({ status: 'error' }); });
    return () => { alive = false; };
  }, [id, excludeKey]);

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
            {k.overBudget && redTag('예산 초과')}
            {k.overSodium && redTag('나트륨 초과')}
            {k.sodiumUnknown && redTag('나트륨 정보 없음')}
          </div>
          <div style={{ fontSize: 12, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>{candLine(c)}</div>
          {!k.allowed && (
            <div style={{ fontSize: 11, color: C.redText, fontVariantNumeric: 'tabular-nums' }}>
              교체 시 한 끼{k.overBudget ? ` 원가 ${won(k.costAfter)} > 예산 ${won(k.budget)}` : ''}
              {k.overSodium && k.sodiumCap != null ? `${k.overBudget ? ',' : ''} 나트륨 ${Math.round(k.sodiumAfter ?? 0)}mg > 상한 ${Math.round(k.sodiumCap)}mg` : ''}
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
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        {revertBox}
        {body}
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: C.muted }}>
        한 끼 예산·나트륨 상한을 넘는 후보는 고를 수 없어요. 3일 중복·반상 구성 같은 나머지 제약의 재검증은 식단 재생성에서 반영돼요
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

  // 교체 후보 제외 목록: 지금 본식단에 올라 있는 메뉴 id(같은 메뉴를 한 식단에 두 번 넣지 않게).
  const planIds = useMemo(
    () => [...new Set(plan.weeks.flatMap((w) => w.days.flatMap((d) => d.cells.flatMap((c) => c.items.map((it) => it.nutritionId)))))]
      .filter((x): x is number => x != null),
    [plan.weeks],
  );

  // 교체·삭제는 칸 식별자(menuId)로 찾고, 바꾼 뒤 셀·총합·달성률·원가를 칸 영양으로 다시 계산한다.
  // ⚠ 제약 재검증(3일 중복·열량 밴드 등)은 하지 않는다 — 재생성(include/exclude 재풀이)은 후속.
  const onDelete = (it: MealItem) => {
    setPlan(recomputePlan(editItems(plan, (items) => items.filter((x) => x.menuId !== it.menuId))));
    message.success(`'${it.name}' 삭제`);
  };
  const onSwap = (it: MealItem, cell: MealCell, c: SwapCandidate) => {
    // 버튼 비활성과 같은 판정을 한 번 더 — 예산·나트륨 상한을 넘기는 교체는 적용하지 않는다.
    if (!checkSwap(plan, cell, it, c).allowed) { message.warning(`'${c.name}'(으)로 바꾸면 한 끼 예산·나트륨 상한을 넘어요`); return; }
    setPlan(recomputePlan(editItems(plan, (items) => items.map((x) => {
      if (x.menuId !== it.menuId) return x;
      const first = x.orig ? { orig: x.orig, origNutritionId: x.origNutritionId, origNutri: x.origNutri }
        : { orig: x.name, origNutritionId: x.nutritionId, origNutri: x.nutri };
      return {
        ...x, name: c.name, nutritionId: c.menu_id, flag: undefined, alt: false,
        nutri: candidateNutri(c), ...first,
      };
    }))));
    message.success(`'${it.name}' → '${c.name}' 교체`);
  };
  const onRevert = (it: MealItem) => {
    setPlan(recomputePlan(editItems(plan, (items) => items.map((x) => (x.menuId !== it.menuId ? x : {
      ...x, name: x.orig ?? x.name, nutritionId: x.origNutritionId, nutri: x.origNutri, flag: undefined,
      orig: undefined, origNutritionId: undefined, origNutri: undefined,
    })))));
    message.success(`'${it.orig}'(으)로 되돌렸어요`);
  };
  const toggleCheck = (label: string) => {
    if (readOnly) return;
    setPlan({ ...plan, checks: plan.checks.map((c) => (c.label === label ? { ...c, done: !c.done } : c)) });
  };

  const MenuLine = ({ it, cell }: { it: MealItem; cell: MealCell }) => {
    if (readOnly) {
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, lineHeight: '20px' }}>
          <span style={{ fontSize: 13, color: it.flag ? C.redText : it.alt ? C.greenText : C.text }}>{it.name}</span>
          {it.alt && <span style={{ fontSize: 10, fontWeight: 600, color: C.greenText, background: '#D2F1DF', borderRadius: 5, padding: '0 5px' }}>대체</span>}
          {it.flag && <span style={{ fontSize: 10, fontWeight: 600, color: C.redText, background: '#FADCDC', borderRadius: 5, padding: '0 5px' }}>{it.flag}</span>}
        </div>
      );
    }
    return (
      <div className="menuline" style={{ display: 'flex', alignItems: 'center', gap: 5, lineHeight: '20px' }}>
        <Popover
          trigger="click" placement="bottom" destroyOnHidden
          title={<span style={{ fontSize: 13 }}>‘{it.name}’ 대신</span>}
          content={<SwapPanel item={it} excludeIds={planIds} check={(c) => checkSwap(plan, cell, it, c)}
            onPick={(c) => onSwap(it, cell, c)} onRevert={() => onRevert(it)} />}
        >
          <span style={{ fontSize: 13, color: it.flag ? C.redText : it.alt ? C.greenText : C.text, cursor: 'pointer' }}>{it.name}</span>
        </Popover>
        {it.alt && <span style={{ fontSize: 10, fontWeight: 600, color: C.greenText, background: '#D2F1DF', borderRadius: 5, padding: '0 5px' }}>대체</span>}
        {it.flag && <span style={{ fontSize: 10, fontWeight: 600, color: C.redText, background: '#FADCDC', borderRadius: 5, padding: '0 5px' }}>{it.flag}</span>}
        <span className="del" style={{ opacity: 0, cursor: 'pointer', color: C.muted, transition: 'opacity .1s' }} onClick={() => onDelete(it)}>
          <CloseOutlined style={{ fontSize: 10 }} />
        </span>
      </div>
    );
  };

  const Cell = ({ cell }: { cell?: MealCell }) => {
    if (!cell) return <td style={{ border: `1px solid ${C.line}`, verticalAlign: 'top' }} />;
    return (
      <td style={{ border: `1px solid ${C.line}`, verticalAlign: 'top', padding: '10px 12px', background: cell.warn ? C.redTint : '#fff', minWidth: 150 }}>
        {cell.items.map((it) => <MenuLine key={it.menuId ?? it.name} it={it} cell={cell} />)}
        <div style={{ marginTop: 8, fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
          <span style={{ fontWeight: 700, color: cell.warn ? C.redText : C.text }}>{cell.kcal} kcal</span>
          <span style={{ color: C.sub, marginLeft: 12 }}>단백 {cell.protein.toFixed(1)}g</span>
        </div>
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
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 316px', gap: 16, alignItems: 'start' }}>
        {/* 식단표 */}
        <Card size="small" styles={{ body: { padding: 20 } }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>주간 식단표</span>
            <div style={{ flex: 1 }} />
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: C.sub }}>
              <span style={{ width: 9, height: 9, borderRadius: 3, background: C.redTint, border: `1px solid ${C.redBorder}` }} />예산·나트륨 초과
            </span>
            <Button size="small" icon={<PrinterOutlined />} onClick={() => window.print()}>인쇄</Button>
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: C.sub }}>{planTargetLabel(plan)} · {planDateRange(plan)} · {plan.headcount}명 · 단위 1인 기준</div>

          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 14, tableLayout: 'fixed' }}>
            {weeks.map((wk) => (
              <tbody key={wk.label}>
                <tr>
                  <th style={{ width: 92, border: `1px solid ${C.line}`, background: C.head, padding: '9px 12px', textAlign: 'left', fontSize: 13, color: C.text }}>{wk.label}</th>
                  {wk.days.map((d) => (
                    <th key={d.date} style={{ border: `1px solid ${C.line}`, background: C.head, padding: '7px 12px', textAlign: 'center', fontWeight: 600 }}>
                      <div style={{ fontSize: 13, color: C.text }}>{d.dow}</div>
                      <div style={{ fontSize: 11, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>{d.date}</div>
                    </th>
                  ))}
                </tr>
                {plan.meals.map((meal) => (
                  <tr key={meal}>
                    <td style={{ border: `1px solid ${C.line}`, background: C.head, padding: '10px 12px', verticalAlign: 'top' }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: C.green }}>{MEAL_TABLE[meal as MealKind]}</div>
                      <div style={{ fontSize: 11, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>{MEAL_TIME[meal as MealKind]}</div>
                    </td>
                    {wk.days.map((d) => <Cell key={d.date + meal} cell={d.cells.find((c) => c.kind === meal)} />)}
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
