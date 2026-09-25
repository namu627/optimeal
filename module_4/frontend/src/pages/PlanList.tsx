// src/pages/PlanList.tsx
// 내 식단 목록 — 저장된 식단(/api/menu/plans)을 최신순 카드로 보여준다. 카드 클릭 → 상세(/plans/:id).
// 로그인 체계가 없어 소유자 구분 없는 전역 목록이다(MVP).
import { useEffect, useState } from 'react';
import { App, Button, Card, Empty, Popconfirm, Skeleton } from 'antd';
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { colors } from '../theme';
import { deleteSavedPlan, formatSavedAt, listSavedPlans, type SavedPlanSummary } from '../api/menu';

type LoadState = { status: 'loading' } | { status: 'error' } | { status: 'ok'; items: SavedPlanSummary[] };

const won = (n: number | null) => (n == null ? '–' : `${Math.round(n).toLocaleString()}원`);

export default function PlanList() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    listSavedPlans()
      .then((items) => { if (alive) setState({ status: 'ok', items }); })
      .catch((e) => { console.error('[식단목록] 조회 실패:', e); if (alive) setState({ status: 'error' }); });
    return () => { alive = false; };
  }, [reloadKey]);

  const reload = () => { setState({ status: 'loading' }); setReloadKey((k) => k + 1); };

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

  if (state.status === 'loading') {
    return <Card><Skeleton active paragraph={{ rows: 4 }} /></Card>;
  }
  if (state.status === 'error') {
    return (
      <Card>
        <Empty description="저장된 식단을 불러오지 못했어요">
          <Button icon={<ReloadOutlined />} onClick={reload}>다시 시도</Button>
        </Empty>
      </Card>
    );
  }
  if (!state.items.length) {
    return (
      <Card styles={{ body: { padding: '56px 20px' } }}>
        <Empty description="저장된 식단이 없어요 — 식단을 만들고 확정 단계에서 '초안으로 저장'을 눌러 보세요">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/plans/new')}>새 식단 만들기</Button>
        </Empty>
      </Card>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 13, color: colors.textSecondary }}>저장된 식단 {state.items.length}개 · 최신순</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
        {state.items.map((p) => {
          const target = p.condition_text?.split(' · ')[0] ?? '대상 미상';
          const period = [p.start_date, p.period_text].filter(Boolean).join(' · ') || '–';
          const over = p.cost_per_person != null && p.budget_per_person != null && p.cost_per_person > p.budget_per_person;
          return (
            <Card key={p.id} hoverable onClick={() => navigate(`/plans/${p.id}`)} styles={{ body: { padding: 18 } }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</div>
                  <div style={{ marginTop: 3, fontSize: 12, color: colors.textTertiary }}>{formatSavedAt(p.created_at)} 저장</div>
                </div>
                <div onClick={(e) => e.stopPropagation()}>
                  <Popconfirm title="이 식단을 삭제할까요?" description="삭제하면 되돌릴 수 없어요." okText="삭제" cancelText="취소"
                    okButtonProps={{ danger: true }} onConfirm={() => remove(p)}>
                    <Button type="text" size="small" icon={<DeleteOutlined />} aria-label={`${p.name} 삭제`} />
                  </Popconfirm>
                </div>
              </div>
              <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 12, rowGap: 6, fontSize: 13 }}>
                <span style={{ color: colors.textSecondary }}>대상</span>
                <span style={{ color: colors.text }}>{target}{p.headcount != null ? ` · ${p.headcount}명` : ''}</span>
                <span style={{ color: colors.textSecondary }}>기간</span>
                <span style={{ color: colors.text }}>{period}</span>
                <span style={{ color: colors.textSecondary }}>1인 원가</span>
                <span style={{ color: over ? colors.errorText : colors.text, fontWeight: 600 }}>
                  {won(p.cost_per_person)}
                  <span style={{ fontWeight: 400, color: colors.textTertiary }}> / 예산 {won(p.budget_per_person)}</span>
                </span>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
