// src/pages/PlanResult.tsx
// 식단 결과 화면 — 주간 달력(요일=열) 레이아웃
// - 상단: 영양 달성률 그래프 + 1인 원가 자리(placeholder) — 박미연 컴포넌트 삽입 예정
// - 식단표: 요일을 열로 놓고(월~일), 각 열 안에서 메뉴를 세로로 나열. 한 주가 한 판에 들어와 스크롤 최소.
// - 메뉴 행: 클릭 시 교체 팝오버 / × 삭제. 경고(나트륨·원가) 빨강, 대체 초록.
// - 일반/대체식 토글, 재생성.
// 데이터: POST /api/menu/generate. 503(백엔드 미통합)이면 목업 폴백(삭제·교체·재생성 로컬 반영).
import { useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Card, Segmented, Tag, Button, Popover, Typography, Alert, Spin, Empty, message } from 'antd';
import { ReloadOutlined, CloseOutlined } from '@ant-design/icons';
import {
  generateMenu, toMealPlan, mockPlan, swapCandidates,
  MEAL_KR, MEAL_DOT,
  type MenuGenerateRequest, type MealPlan, type MealDay, type MealItem,
} from '../api/menu';

const { Text } = Typography;
const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7', line: '#EEF2F0',
  redTint: '#FDECEC', redBorder: '#F5BEC0', redText: '#B42318', red: '#E5484D',
  green: '#12A150', greenTint: '#E4F7EB', greenText: '#0B6B36',
};

const DEFAULT_REQ: MenuGenerateRequest = {
  profile_key: 'elem_low_mix',
  days: 7,
  meals: ['점심'],
  with_alternatives: true,
  budget_limit_per_person: 4500,
  allergy_groups: [{ label: '난류·우유', allergens: ['난류', '우유'], count: 3 }],
};

/* 목업 로컬 편집 헬퍼 (전체 트랙에서 menuId 로 찾아 갱신) */
function editItems(plan: MealPlan, fn: (items: MealItem[]) => MealItem[]): MealPlan {
  const mapDays = (days: MealDay[]): MealDay[] =>
    days.map((d) => ({ ...d, cells: d.cells.map((c) => ({ ...c, items: fn(c.items) })) }));
  return { ...plan, days: mapDays(plan.days), alternatives: plan.alternatives.map((t) => ({ ...t, days: mapDays(t.days) })) };
}

/** 상단 슬롯: 박미연이 넘겨줄 <영양 달성률 그래프 + 1인 원가> 자리 */
function NutritionSlot() {
  return (
    <Card size="small" styles={{ body: { padding: 0 } }}>
      <div
        style={{
          height: 116, display: 'flex', flexDirection: 'column', gap: 4, textAlign: 'center',
          alignItems: 'center', justifyContent: 'center', margin: 12,
          border: `1px dashed ${C.border}`, borderRadius: 8, color: C.muted,
        }}
      >
        <div style={{ fontWeight: 600, color: C.sub }}>영양 달성률 그래프 · 1인 원가</div>
        <div style={{ fontSize: 12 }}>그래프 컴포넌트 자리 (박미연 전달 후 삽입) — props: plan.days 영양 합산 · total_cost</div>
      </div>
    </Card>
  );
}

function MenuRow({ it, onSwap, onDelete }: {
  it: MealItem; onSwap: (it: MealItem, to: string) => void; onDelete: (it: MealItem) => void;
}) {
  const alt = it.flags?.includes('대체');
  const warn = it.flags?.some((f) => f === '나트륨' || f === '원가');
  return (
    <div
      style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px',
        borderTop: `1px solid ${C.line}`, background: warn ? C.redTint : alt ? C.greenTint : 'transparent',
      }}
    >
      <Popover
        trigger="click"
        title={`'${it.name}' 대신`}
        content={
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: 190 }}>
            {swapCandidates().map((cand) => (
              <div key={cand} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ flex: 1, fontSize: 13 }}>{cand}</span>
                <Button size="small" type="primary" onClick={() => onSwap(it, cand)}>교체</Button>
              </div>
            ))}
          </div>
        }
      >
        <span
          title={it.name}
          style={{ flex: 1, minWidth: 0, fontSize: 13, color: warn ? C.redText : C.text, cursor: 'pointer', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {it.name}
        </span>
      </Popover>
      {it.kcal != null && (
        <span style={{ fontSize: 11, color: C.muted, fontVariantNumeric: 'tabular-nums', flex: 'none' }}>{it.kcal}kcal</span>
      )}
      {it.flags?.map((f) => (
        <span key={f} style={{ fontSize: 10, fontWeight: 600, borderRadius: 5, padding: '1px 4px', flex: 'none', color: f === '대체' ? C.greenText : C.redText, background: f === '대체' ? '#D2F1DF' : '#FADCDC' }}>{f}</span>
      ))}
      <span
        onClick={() => onDelete(it)}
        title="삭제"
        style={{ display: 'inline-flex', width: 18, height: 18, borderRadius: 9, alignItems: 'center', justifyContent: 'center', cursor: 'pointer', background: '#F2F6F3', color: C.sub, flex: 'none' }}
      >
        <CloseOutlined style={{ fontSize: 10 }} />
      </span>
    </div>
  );
}

function DayColumn({ day, multiMeal, onSwap, onDelete }: {
  day: MealDay; multiMeal: boolean;
  onSwap: (it: MealItem, to: string) => void; onDelete: (it: MealItem) => void;
}) {
  return (
    <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, background: '#fff', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* 열 헤더: 날짜·요일·총 kcal */}
      <div style={{ padding: '9px 11px 8px', background: day.anyWarn ? '#FEF6F6' : '#F7FAF8' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>{day.dow}</span>
          <span style={{ fontSize: 12, color: C.muted, fontVariantNumeric: 'tabular-nums' }}>{day.date}</span>
          <div style={{ flex: 1 }} />
          {day.anyWarn && <span style={{ width: 7, height: 7, borderRadius: 4, background: C.red }} />}
        </div>
        <div style={{ marginTop: 3, fontSize: 12, color: day.anyWarn ? C.redText : C.sub, fontVariantNumeric: 'tabular-nums' }}>
          {day.totalKcal.toLocaleString()} kcal
        </div>
      </div>

      {/* 끼니별 메뉴 (세로 나열) */}
      {day.cells.map((c) => (
        <div key={c.kind}>
          {multiMeal && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px 2px', borderTop: `1px solid ${C.line}` }}>
              <span style={{ width: 7, height: 7, borderRadius: 3, background: MEAL_DOT[c.kind] }} />
              <span style={{ fontSize: 11, fontWeight: 600, color: C.sub }}>{MEAL_KR[c.kind]} · {c.kcal}kcal</span>
            </div>
          )}
          {c.items.map((it) => (
            <MenuRow key={it.menuId ?? it.name} it={it} onSwap={onSwap} onDelete={onDelete} />
          ))}
          <div style={{ padding: '6px 10px', borderTop: `1px solid ${C.line}`, fontSize: 11, color: c.warn ? C.redText : C.muted }}>
            {c.note}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function PlanResult() {
  const location = useLocation();
  const stateReq = (location.state as { request?: MenuGenerateRequest } | null)?.request;
  const [req] = useState<MenuGenerateRequest>(stateReq ?? DEFAULT_REQ);

  const [plan, setPlan] = useState<MealPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<'normal' | 'alt'>('normal');
  const [altIdx, setAltIdx] = useState(0);
  const [seed, setSeed] = useState(0);

  async function run(nextSeed = seed) {
    setLoading(true);
    try {
      const raw = await generateMenu({ ...req });
      setPlan(toMealPlan(raw, req)); // 실제 응답 매핑(미구현 시 예외 → 목업)
    } catch {
      setPlan(mockPlan(req, nextSeed)); // 503 또는 매핑 미구현 → 목업 폴백
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void run(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const regenerate = () => {
    const s = seed + 1;
    setSeed(s);
    void run(s);
  };

  const onDelete = (it: MealItem) => {
    if (!plan) return;
    if (plan.source === 'mock') {
      setPlan(editItems(plan, (items) => items.filter((x) => x.menuId !== it.menuId)));
      message.success(`'${it.name}' 삭제`);
    } else {
      void regenerate(); // TODO(live): exclude_menu_ids 에 it.menuId 추가해 generate 재호출
    }
  };
  const onSwap = (it: MealItem, to: string) => {
    if (!plan) return;
    if (plan.source === 'mock') {
      setPlan(editItems(plan, (items) => items.map((x) => (x.menuId === it.menuId ? { ...x, name: to, flags: x.flags?.filter((f) => f === '대체') } : x))));
      message.success(`'${it.name}' → '${to}' 교체`);
    } else {
      void regenerate(); // TODO(live): exclude(old)+include(new) 로 generate 재호출
    }
  };

  const activeDays = useMemo(() => {
    if (view === 'alt' && plan?.alternatives.length) return plan.alternatives[altIdx]?.days ?? plan.days;
    return plan?.days ?? [];
  }, [view, altIdx, plan]);

  const multiMeal = (plan?.meals.length ?? 1) > 1;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <NutritionSlot />

      {plan?.source === 'mock' && (
        <Alert
          type="warning" showIcon
          title="백엔드(식단 생성) 준비 중 — 미리보기(목업)로 표시 중"
          description="CSP(모듈 3)가 develop 통합되면 실제 식단으로 대체됩니다. 지금은 삭제·교체·재생성이 로컬에서만 반영돼요."
        />
      )}

      {/* 조건 + 생성 근거 + 토글 */}
      <Card size="small">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <Text style={{ fontSize: 14, fontWeight: 600, color: C.text }}>{plan?.periodLabel}</Text>
          <Text style={{ fontSize: 12, color: C.sub }}>{plan?.targetLabel}</Text>
          <div style={{ flex: 1 }} />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={regenerate}>재생성</Button>
          <Segmented
            value={view}
            onChange={(v) => setView(v as 'normal' | 'alt')}
            options={[
              { label: '일반식', value: 'normal' },
              { label: '대체식', value: 'alt', disabled: !plan?.alternatives.length },
            ]}
          />
        </div>

        {plan?.rationale?.length ? (
          <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <Text style={{ fontSize: 12, color: C.muted, marginRight: 2 }}>생성 근거</Text>
            {plan.rationale.map((r) => <Tag key={r} style={{ marginInlineEnd: 0 }}>{r}</Tag>)}
          </div>
        ) : null}

        {view === 'alt' && (plan?.alternatives.length ?? 0) > 0 && (
          <div style={{ marginTop: 10 }}>
            <Segmented
              value={altIdx}
              onChange={(v) => setAltIdx(Number(v))}
              options={plan!.alternatives.map((t, i) => ({ label: `${t.label} ${t.count}명`, value: i }))}
            />
          </div>
        )}
      </Card>

      {/* 식단표 — 주간 달력 (요일=열). 좁으면 가로 스크롤. */}
      {loading ? (
        <Card size="small"><div style={{ padding: 48, textAlign: 'center' }}><Spin /></div></Card>
      ) : !plan || activeDays.length === 0 ? (
        <Card size="small">
          <Empty description="조건을 만족하는 식단이 없어요 (INFEASIBLE) — 예산·나트륨·끼니 조건을 완화해 보세요" />
        </Card>
      ) : (
        <>
          <div style={{ overflowX: 'auto', paddingBottom: 4 }}>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: `repeat(${activeDays.length}, minmax(156px, 1fr))`,
                gap: 12,
                minWidth: activeDays.length * 156,
                alignItems: 'start',
              }}
            >
              {activeDays.map((d) => (
                <DayColumn key={d.date} day={d} multiMeal={multiMeal} onSwap={onSwap} onDelete={onDelete} />
              ))}
            </div>
          </div>
          {/* 범례 */}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', fontSize: 12, color: C.muted, padding: '0 2px' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: 4, background: C.red }} /> 확인 필요 (예산·나트륨 초과)
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: 4, background: C.green }} /> 대체식
            </span>
            <span>메뉴 클릭 → 교체 · × → 삭제</span>
          </div>
        </>
      )}
    </div>
  );
}
