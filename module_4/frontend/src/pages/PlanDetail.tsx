// src/pages/PlanDetail.tsx
// 저장된 식단 열람(/plans/:id) — 검토 화면(Step2Review)을 읽기 전용으로 재사용한다.
import { useEffect, useState } from 'react';
import { Button, Card, Empty, Skeleton } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { colors } from '../theme';
import Step2Review from './plan/Step2Review';
import { formatSavedAt, getSavedPlan, type SavedPlan } from '../api/menu';

type LoadState = { status: 'loading' } | { status: 'notfound' } | { status: 'error' } | { status: 'ok'; saved: SavedPlan };

export default function PlanDetail() {
  const { id } = useParams();
  return <PlanDetailView key={id} id={Number(id)} />;
}

function PlanDetailView({ id }: { id: number }) {
  const navigate = useNavigate();
  const [state, setState] = useState<LoadState>(Number.isInteger(id) ? { status: 'loading' } : { status: 'notfound' });

  useEffect(() => {
    if (!Number.isInteger(id)) return;
    let alive = true;
    getSavedPlan(id)
      .then((saved) => { if (alive) setState({ status: 'ok', saved }); })
      .catch((e) => {
        console.error('[식단상세] 조회 실패:', e);
        if (alive) setState({ status: e?.response?.status === 404 ? 'notfound' : 'error' });
      });
    return () => { alive = false; };
  }, [id]);

  const back = <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/plans')}>목록으로</Button>;

  if (state.status === 'loading') return <Card><Skeleton active paragraph={{ rows: 6 }} /></Card>;
  if (state.status !== 'ok') {
    return (
      <Card>
        <Empty description={state.status === 'notfound' ? '저장된 식단을 찾을 수 없어요 (삭제되었을 수 있어요)' : '식단을 불러오지 못했어요'}>
          {back}
        </Empty>
      </Card>
    );
  }

  const { saved } = state;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        {back}
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: colors.text }}>{saved.name}</div>
          <div style={{ fontSize: 12, color: colors.textTertiary }}>{formatSavedAt(saved.created_at)} 저장 · 읽기 전용</div>
        </div>
      </div>
      <Step2Review plan={saved.plan} readOnly />
    </div>
  );
}
