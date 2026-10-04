import { colors } from '../theme';

/**
 * 식단 생성 3단계 진행 표시. 시안 04·05·06 상단 공통.
 * 지난 단계는 체크, 현재 단계는 브랜드 그린, 다음 단계는 회색.
 */
export interface StepperProps {
  /** 1 | 2 | 3 — 현재 단계 */
  current: 1 | 2 | 3;
  /** 단계 클릭으로 이동시킬 때. 지난 단계만 눌린다. */
  onStepClick?: (step: 1 | 2 | 3) => void;
}

const STEPS = [
  { no: 1, label: '조건 입력' },
  { no: 2, label: '검토' },
  { no: 3, label: '확정' },
] as const;

export default function Stepper({ current, onStepClick }: StepperProps) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      {STEPS.map(({ no, label }, i) => {
        const done = no < current;
        const active = no === current;
        const clickable = done && !!onStepClick;

        return (
          <div key={no} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div
              onClick={clickable ? () => onStepClick?.(no as 1 | 2 | 3) : undefined}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                cursor: clickable ? 'pointer' : 'default',
              }}
            >
              <span
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: '50%',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 12,
                  fontWeight: 600,
                  background: active || done ? colors.primary : colors.borderSubtle,
                  color: active || done ? '#FFFFFF' : colors.textTertiary,
                }}
              >
                {done ? '✓' : no}
              </span>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: active ? 600 : 400,
                  color: active ? colors.text : done ? colors.textSecondary : colors.textTertiary,
                }}
              >
                {label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <span style={{ width: 28, height: 1, background: colors.border }} />
            )}
          </div>
        );
      })}
    </div>
  );
}