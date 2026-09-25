// src/pages/plan/Step2Review.tsx
// 식단 생성 2단계 · 검토 (시안 화면 5 / 5-a 3끼 / 5-b 대체식 / 5-c 교체 팝오버)
import { useMemo, useState } from 'react';
import { Card, Button, Popover, Progress, App } from 'antd';
import { PrinterOutlined, CloseOutlined, CheckOutlined } from '@ant-design/icons';
import StepIndicator from './StepIndicator';
import KpiRow from '../../components/KpiRow';
import {
  MEAL_TABLE, MEAL_TIME, swapCandidates, planDateRange, planTargetLabel,
  type MealPlan, type WeekBlock, type MealItem, type MealKind, type MealCell,
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

export default function Step2Review({ plan, setPlan, onPrev, onNext, onEditConditions }: {
  plan: MealPlan; setPlan: (p: MealPlan) => void; onPrev: () => void; onNext: () => void; onEditConditions: () => void;
}) {
  const { message } = App.useApp();
  const [view, setView] = useState<'normal' | 'alt'>('normal');
  const [track, setTrack] = useState(0);

  const weeks = useMemo(
    () => (view === 'alt' && plan.alternatives.length ? plan.alternatives[track].weeks : plan.weeks),
    [view, track, plan],
  );
  const doneChecks = plan.checks.filter((c) => c.done).length;

  const onDelete = (it: MealItem) => {
    setPlan(editItems(plan, (items) => items.filter((x) => x.menuId !== it.menuId)));
    message.success(`'${it.name}' 삭제`);
  };
  const onSwap = (it: MealItem, to: string) => {
    setPlan(editItems(plan, (items) => items.map((x) => {
      if (x.menuId !== it.menuId) return x;
      const revert = to === x.orig;
      return { ...x, name: to, flag: undefined, alt: false, orig: revert ? undefined : (x.orig ?? x.name) };
    })));
    message.success(`'${it.name}' → '${to}' 교체`);
  };
  const toggleCheck = (label: string) =>
    setPlan({ ...plan, checks: plan.checks.map((c) => (c.label === label ? { ...c, done: !c.done } : c)) });

  const MenuLine = ({ it }: { it: MealItem }) => {
    const cand = swapCandidates(it.name);
    const canRevert = !!it.orig && it.orig !== it.name;
    return (
      <div className="menuline" style={{ display: 'flex', alignItems: 'center', gap: 5, lineHeight: '20px' }}>
        <Popover
          trigger="click" placement="bottom"
          title={<span style={{ fontSize: 13 }}>‘{it.name}’ 대신</span>}
          content={
            <div style={{ width: 300 }}>
              <div style={{ fontSize: 12, color: C.sub, marginBottom: 8 }}>{cand.sub}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                {canRevert && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, border: `1px solid ${C.tint}`, background: C.tintBg, borderRadius: 10, padding: '9px 11px' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: C.text }}>{it.orig}</div>
                      <div style={{ fontSize: 12, color: C.sub }}>원래 메뉴로 되돌리기</div>
                    </div>
                    <Button size="small" onClick={() => onSwap(it, it.orig!)}>되돌리기</Button>
                  </div>
                )}
                {cand.list.map((c) => (
                  <div key={c.name} style={{ display: 'flex', alignItems: 'center', gap: 10, border: `1px solid ${C.line}`, borderRadius: 10, padding: '9px 11px' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: C.text }}>{c.name}</div>
                      <div style={{ fontSize: 12, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>{c.kcal} kcal · {c.cost.toLocaleString()}원 · {c.sodium}mg</div>
                    </div>
                    <Button size="small" type="primary" onClick={() => onSwap(it, c.name)}>교체</Button>
                  </div>
                ))}
              </div>
            </div>
          }
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
        {cell.items.map((it) => <MenuLine key={it.menuId ?? it.name} it={it} />)}
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
        <StepIndicator current={2} />
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
          <Button size="small" onClick={onEditConditions}>조건 수정</Button>
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
          <div style={{ marginTop: 12, fontSize: 12, color: C.muted }}>메뉴에 마우스를 올리면 교체·삭제할 수 있어요 · 1인 기준 열량(kcal)과 단백질(g)</div>
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
                <div key={c.label} onClick={() => toggleCheck(c.label)} style={{ display: 'flex', alignItems: 'center', gap: 11, height: 40, cursor: 'pointer' }}>
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

      <div style={{ display: 'flex', alignItems: 'center' }}>
        <Button onClick={onPrev}>이전</Button>
        <div style={{ flex: 1 }} />
        <Button type="primary" onClick={onNext}>다음: 확정</Button>
      </div>

      <style>{`.menuline:hover .del{opacity:1 !important}`}</style>
    </div>
  );
}
