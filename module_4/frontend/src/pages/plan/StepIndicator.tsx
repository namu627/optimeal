// src/pages/plan/StepIndicator.tsx
// 식단 생성 3단계 진행 표시 (조건 입력 → 검토 → 확정)
import { Fragment } from 'react';
import { CheckOutlined } from '@ant-design/icons';

const C = { green: '#12A150', greenText: '#0B6B36', tint: '#E4F7EB', muted: '#98A5A0', line: '#DCE3DF', text: '#16211C' };
const STEPS = [
  { n: 1, label: '조건 입력' },
  { n: 2, label: '검토' },
  { n: 3, label: '확정' },
];

export default function StepIndicator({ current }: { current: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
      {STEPS.map((s, i) => {
        const done = s.n < current;
        const active = s.n === current;
        return (
          <Fragment key={s.n}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div
                style={{
                  width: 24, height: 24, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 12, fontWeight: 700,
                  background: done ? C.tint : active ? C.green : '#EEF2F0',
                  color: done ? C.greenText : active ? '#fff' : C.muted,
                }}
              >
                {done ? <CheckOutlined style={{ fontSize: 12, color: C.green }} /> : s.n}
              </div>
              <span style={{ fontSize: 14, fontWeight: active ? 700 : 400, color: active ? C.text : done ? C.greenText : C.muted }}>
                {s.label}
              </span>
            </div>
            {i < STEPS.length - 1 && <div style={{ width: 56, height: 1, background: s.n < current ? '#BFEACF' : C.line }} />}
          </Fragment>
        );
      })}
    </div>
  );
}
