// src/pages/PlanList.tsx
// 식단 목록 — 시안 화면 3(기본) / 3-a(결과 없음). 원본: 02_화면시안 PlanListBody + png 03·03a
// 저장된 식단(/api/menu/plans)을 표로 보여주고 검색·기간·대상·확정 여부로 거른다.
// '복제해서 만들기'는 1단계 조건 입력으로 이동해 저장본의 조건을 채운다(PlanCreate · cloneId).
import { useEffect, useMemo, useState } from 'react';
import { App, Button, Card, Empty, Popconfirm, Select, Skeleton } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { deleteSavedPlan, listSavedPlans, type PlanStatus, type SavedPlanSummary } from '../api/menu';

// 시안 값 그대로
const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', count: '#8A9691',
  border: '#E5EAE7', inputBorder: '#DCE3DF', line: '#EEF2F0', head: '#F7FAF8',
  tint: '#E4F7EB', tintHover: '#D2F1DF', greenText: '#0B6B36',
  over: '#B42318', under: '#8A6A1E', dangerHover: '#FDECEC',
};
const GRID = '96px 1fr 70px 66px 84px 96px 92px 248px';
const PAGE_SIZE = 6;
// 대상별 점 색 — 시안 카테고리 색(초등 청록 · 중학 보라 · 유치원/고등 노랑 · 노인 연두)
const DOT: Record<string, string> = { 초등학생: '#06B6D4', 중학생: '#8B5CF6', 고등학생: '#F5B301', 유치원: '#F5B301', 노인: '#84CC16' };
const BADGE: Record<PlanStatus, { c: string; bg: string }> = {
  확정: { c: '#0B6B36', bg: '#E4F7EB' },
  초안: { c: '#5D6B64', bg: '#EEF2F0' },
};

type LoadState = { status: 'loading' } | { status: 'error' } | { status: 'ok'; items: SavedPlanSummary[] };
type PeriodKey = '1m' | '3m' | '6m' | 'all';
type StatusKey = '전체' | PlanStatus;
const PERIOD_OPTIONS: { value: PeriodKey; label: string }[] = [
  { value: '1m', label: '최근 1개월' }, { value: '3m', label: '최근 3개월' },
  { value: '6m', label: '최근 6개월' }, { value: 'all', label: '전체 기간' },
];
const PERIOD_MONTHS = { '1m': 1, '3m': 3, '6m': 6 } as const;
const DEFAULT_FILTER = { query: '', period: '3m' as PeriodKey, target: 'all', status: '전체' as StatusKey };

// condition_text 예: '초등학생 · 320명 · 평일 7일 · 중식 · 알레르기 2그룹 · 예산 4,500원/식'
const targetOf = (p: SavedPlanSummary) => p.condition_text?.split(' · ')[0] || '대상 미상';
const mealOf = (p: SavedPlanSummary) =>
  p.condition_text?.split(' · ').find((t) => /^(조식|중식|석식)(·(조식|중식|석식))*$/.test(t)) ?? '';
const statusOf = (p: SavedPlanSummary): PlanStatus => (p.status === '확정' ? '확정' : '초안');
const won = (n: number | null | undefined) => (n == null ? '–' : `${Math.round(n).toLocaleString()}원`);
const rateColor = (v: number) => (v > 110 ? C.over : v < 90 ? C.under : C.text); // 목표 ±10% 밖만 색
const mmdd = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '–' : `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
};
function withinPeriod(iso: string, period: PeriodKey) {
  if (period === 'all') return true;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return true;
  const from = new Date();
  from.setMonth(from.getMonth() - PERIOD_MONTHS[period]);
  return d >= from;
}

const SearchIcon = () => (
  <svg width="15" height="15" viewBox="0 0 18 18" fill="none" stroke={C.muted} strokeWidth="1.6" strokeLinecap="round"><circle cx="8" cy="8" r="5" /><path d="M11.8 11.8 15.5 15.5" /></svg>
);
const CopyIcon = () => (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="2.25" y="2.25" width="7" height="7" rx="2" /><path d="M4.75 11.75h7v-7" /></svg>
);
const TrashIcon = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="#8A9691" strokeWidth="1.6" strokeLinecap="round"><path d="M2.5 4h9M5.5 4V2.5h3V4M3.75 4l.6 8h5.3l.6-8" /></svg>
);

export default function PlanList() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);
  const [filter, setFilter] = useState(DEFAULT_FILTER);
  const [page, setPage] = useState(1);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listSavedPlans(200)
      .then((items) => { if (alive) setState({ status: 'ok', items }); })
      .catch((e) => { console.error('[식단목록] 조회 실패:', e); if (alive) setState({ status: 'error' }); });
    return () => { alive = false; };
  }, [reloadKey]);

  const items = useMemo(() => (state.status === 'ok' ? state.items : []), [state]);
  const targetOptions = useMemo(
    () => [{ value: 'all', label: '대상 전체' }, ...[...new Set(items.map(targetOf))].map((t) => ({ value: t, label: t }))],
    [items],
  );
  const rows = useMemo(() => {
    const q = filter.query.trim().toLowerCase();
    return items.filter((p) =>
      withinPeriod(p.created_at, filter.period)
      && (filter.target === 'all' || targetOf(p) === filter.target)
      && (filter.status === '전체' || statusOf(p) === filter.status)
      && (!q || p.name.toLowerCase().includes(q) || targetOf(p).toLowerCase().includes(q)));
  }, [items, filter]);

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const curPage = Math.min(page, pageCount);
  const pageRows = rows.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE);
  const countText = rows.length ? `총 ${rows.length}건 중 ${pageRows.length}건` : '0건';

  const updateFilter = (patch: Partial<typeof DEFAULT_FILTER>) => { setFilter((f) => ({ ...f, ...patch })); setPage(1); };
  const resetFilter = () => { setFilter(DEFAULT_FILTER); setPage(1); };
  const reload = () => { setState({ status: 'loading' }); setReloadKey((k) => k + 1); };
  const clone = (p: SavedPlanSummary) => navigate('/plans/new', { state: { cloneId: p.id } });
  const remove = async (p: SavedPlanSummary) => {
    try {
      await deleteSavedPlan(p.id);
    } catch (e) {
      console.error('[식단목록] 삭제 실패:', e);
      message.error('식단을 삭제하지 못했습니다.');
      return;
    }
    message.success(`'${p.name}' 식단을 삭제했어요`);
    setState((s) => (s.status === 'ok' ? { status: 'ok', items: s.items.filter((x) => x.id !== p.id) } : s));
  };

  if (state.status === 'loading') return <Card><Skeleton active paragraph={{ rows: 6 }} /></Card>;
  if (state.status === 'error') {
    return (
      <Card>
        <Empty description="저장된 식단을 불러오지 못했어요">
          <Button icon={<ReloadOutlined />} onClick={reload}>다시 시도</Button>
        </Empty>
      </Card>
    );
  }
  if (!items.length) {
    return (
      <Card styles={{ body: { padding: '56px 20px' } }}>
        <Empty description="저장된 식단이 없어요 — 식단을 만들고 확정 단계에서 '초안으로 저장'을 눌러 보세요">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/plans/new')}>새 식단 만들기</Button>
        </Empty>
      </Card>
    );
  }

  const tintBtn = (key: string): React.CSSProperties => ({
    display: 'flex', alignItems: 'center', gap: 6, height: 32, padding: '0 13px', borderRadius: 10, border: 'none',
    background: hover === key ? C.tintHover : C.tint, color: C.greenText, font: '600 12px Pretendard,sans-serif', cursor: 'pointer',
  });

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 16, fontFamily: 'Pretendard,sans-serif' }}>
      {/* 복제 안내 (png 03) */}
      <div style={{ background: '#fff', border: `1px solid ${C.border}`, borderRadius: 14, padding: '16px 20px', display: 'flex', alignItems: 'center', gap: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ font: '600 14px Pretendard,sans-serif', color: C.text }}>지난 식단을 복제해 다시 만들기</div>
          <div style={{ marginTop: 4, font: '400 12px Pretendard,sans-serif', color: C.sub }}>대상·기간·예산만 바꿔 같은 구성으로 재생성해요. 가장 자주 쓰는 방법이에요</div>
        </div>
        <button type="button" style={tintBtn('hero')} onMouseEnter={() => setHover('hero')} onMouseLeave={() => setHover(null)} onClick={() => clone(items[0])}>
          <CopyIcon />최근 식단 복제
        </button>
      </div>

      {/* 필터 바 */}
      <div style={{ background: '#fff', border: `1px solid ${C.border}`, borderRadius: 14, padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 10, flex: 'none' }}>
        <div style={{ width: 250, height: 36, border: `1px solid ${C.inputBorder}`, borderRadius: 10, display: 'flex', alignItems: 'center', gap: 9, padding: '0 12px', boxSizing: 'border-box' }}>
          <SearchIcon />
          <input value={filter.query} onChange={(e) => updateFilter({ query: e.target.value })} placeholder="식단 이름·대상 검색"
            style={{ flex: 1, minWidth: 0, border: 'none', outline: 'none', background: 'transparent', font: '400 13px Pretendard,sans-serif', color: C.text }} />
        </div>
        <Select value={filter.period} options={PERIOD_OPTIONS} onChange={(v) => updateFilter({ period: v })} style={{ height: 36, minWidth: 116 }} />
        <Select value={filter.target} options={targetOptions} onChange={(v) => updateFilter({ target: v })} style={{ height: 36, minWidth: 104 }} />
        <div style={{ display: 'flex', background: C.line, borderRadius: 10, padding: 3 }}>
          {(['전체', '확정', '초안'] as StatusKey[]).map((s) => {
            const on = filter.status === s;
            return (
              <div key={s} onClick={() => updateFilter({ status: s })}
                style={{ height: 30, padding: '0 14px', borderRadius: 8, display: 'flex', alignItems: 'center', cursor: 'pointer',
                  font: `${on ? 600 : 400} 12px Pretendard,sans-serif`, color: on ? C.text : C.sub, background: on ? '#fff' : 'transparent' }}>{s}</div>
            );
          })}
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ font: '400 12px Pretendard,sans-serif', color: C.count, fontVariantNumeric: 'tabular-nums' }}>{countText}</div>
      </div>

      {rows.length > 0 ? (
        <div style={{ background: '#fff', border: `1px solid ${C.border}`, borderRadius: 16, overflow: 'hidden', flex: 'none' }}>
          <div style={{ display: 'grid', gridTemplateColumns: GRID, background: C.head, borderBottom: `1px solid ${C.line}`, padding: '0 24px', height: 42, alignItems: 'center', font: '500 12px Pretendard,sans-serif', color: C.sub }}>
            <div>생성일</div><div>대상</div><div style={{ textAlign: 'right' }}>인원</div><div style={{ textAlign: 'right' }}>기간</div>
            <div style={{ textAlign: 'right' }}>달성률</div><div style={{ textAlign: 'right' }}>1인 원가</div><div style={{ paddingLeft: 16 }}>확정 여부</div><div />
          </div>
          {pageRows.map((p) => {
            const st = statusOf(p);
            const k = `row${p.id}`;
            return (
              <div key={p.id} onClick={() => navigate(`/plans/${p.id}`)} onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)}
                style={{ display: 'grid', gridTemplateColumns: GRID, padding: '0 24px', height: 56, alignItems: 'center', borderBottom: `1px solid ${C.line}`,
                  font: '400 14px Pretendard,sans-serif', color: C.text, fontVariantNumeric: 'tabular-nums', cursor: 'pointer', background: hover === k ? C.head : '#fff' }}>
                <div style={{ color: C.sub }}>{mmdd(p.created_at)}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }} title={p.name}>
                  <span style={{ width: 8, height: 8, borderRadius: 3, background: DOT[targetOf(p)] ?? C.muted, flex: 'none' }} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{targetOf(p)} {mealOf(p)}</span>
                </div>
                <div style={{ textAlign: 'right' }}>{p.headcount == null ? '–' : p.headcount.toLocaleString()}</div>
                <div style={{ textAlign: 'right' }}>{p.total_days == null ? '–' : `${p.total_days}일`}</div>
                <div style={{ textAlign: 'right', color: p.kcal_rate == null ? C.muted : rateColor(p.kcal_rate) }}>{p.kcal_rate == null ? '–' : `${Math.round(p.kcal_rate)}%`}</div>
                <div style={{ textAlign: 'right' }}>{won(p.cost_per_person)}</div>
                <div style={{ paddingLeft: 16 }}>
                  <span style={{ font: '600 12px Pretendard,sans-serif', color: BADGE[st].c, background: BADGE[st].bg, borderRadius: 8, padding: '3px 9px' }}>{st}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8 }} onClick={(e) => e.stopPropagation()}>
                  <button type="button" style={tintBtn(`c${p.id}`)} onMouseEnter={() => setHover(`c${p.id}`)} onMouseLeave={() => setHover(k)} onClick={() => clone(p)}>
                    <CopyIcon />복제해서 만들기
                  </button>
                  <button type="button" onClick={() => navigate(`/plans/${p.id}`)} onMouseEnter={() => setHover(`o${p.id}`)} onMouseLeave={() => setHover(k)}
                    style={{ height: 32, padding: '0 12px', borderRadius: 10, border: `1px solid ${C.inputBorder}`, background: hover === `o${p.id}` ? C.head : '#fff', color: C.text, font: '400 12px Pretendard,sans-serif', cursor: 'pointer' }}>열기</button>
                  <Popconfirm title="이 식단을 삭제할까요?" description="삭제하면 되돌릴 수 없어요." okText="삭제" cancelText="취소" okButtonProps={{ danger: true }} onConfirm={() => remove(p)}>
                    <button type="button" aria-label={`${p.name} 삭제`} onMouseEnter={() => setHover(`d${p.id}`)} onMouseLeave={() => setHover(k)}
                      style={{ width: 32, height: 32, borderRadius: 10, border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', background: hover === `d${p.id}` ? C.dangerHover : 'transparent', cursor: 'pointer' }}><TrashIcon /></button>
                  </Popconfirm>
                </div>
              </div>
            );
          })}
          <div style={{ padding: '16px 24px', display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ font: '400 12px Pretendard,sans-serif', color: C.muted }}>복제하면 1단계 조건 입력으로 이동해 인원·기간만 바꿔 재생성할 수 있어요</div>
            <div style={{ flex: 1 }} />
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', font: '400 12px Pretendard,sans-serif', color: C.sub, fontVariantNumeric: 'tabular-nums' }}>
              {Array.from({ length: pageCount }, (_, i) => i + 1).map((n) => (
                <div key={n} onClick={() => setPage(n)} onMouseEnter={() => setHover(`p${n}`)} onMouseLeave={() => setHover(null)}
                  style={{ width: 28, height: 28, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', boxSizing: 'border-box',
                    ...(n === curPage ? { border: `1px solid ${C.inputBorder}`, background: '#fff', color: C.text } : { background: hover === `p${n}` ? C.head : 'transparent' }) }}>{n}</div>
              ))}
            </div>
          </div>
        </div>
      ) : (
        // 시안 3-a · 결과 없음
        <div style={{ background: '#fff', border: `1px solid ${C.border}`, borderRadius: 16, flex: 1, minHeight: 420, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
          <svg width="150" height="104" viewBox="0 0 150 104" fill="none">
            <rect x="24" y="16" width="102" height="72" rx="14" fill="#F5F8F6" />
            <rect x="40" y="34" width="44" height="7" rx="3.5" fill="#DCE3DF" />
            <rect x="40" y="49" width="68" height="7" rx="3.5" fill="#EEF2F0" />
            <rect x="40" y="64" width="30" height="7" rx="3.5" fill="#EEF2F0" />
            <circle cx="118" cy="80" r="16" fill="#fff" stroke="#DCE3DF" strokeWidth="2" />
            <path d="M113 80h10" stroke="#98A5A0" strokeWidth="2.2" strokeLinecap="round" />
          </svg>
          <div style={{ marginTop: 12, font: '600 16px Pretendard,sans-serif', color: C.text }}>조건에 맞는 식단이 없어요</div>
          <div style={{ font: '400 13px Pretendard,sans-serif', color: C.sub }}>기간이나 대상 필터를 넓혀 보세요</div>
          <button type="button" onClick={resetFilter} onMouseEnter={() => setHover('reset')} onMouseLeave={() => setHover(null)}
            style={{ marginTop: 10, height: 36, padding: '0 16px', borderRadius: 10, border: `1px solid ${C.inputBorder}`, background: hover === 'reset' ? C.head : '#fff', color: C.text, font: '400 13px Pretendard,sans-serif', cursor: 'pointer' }}>필터 초기화</button>
        </div>
      )}
    </div>
  );
}