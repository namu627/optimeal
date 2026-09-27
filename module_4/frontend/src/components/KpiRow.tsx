import { Skeleton } from 'antd';
import type { CSSProperties, ReactNode } from 'react';
import Gauge from './Gauge';
import { gaugeColor, gaugeStatus, type GaugeMode } from './gaugeStatus';
import { colors, radius } from '../theme';

/** 목표 대비 실제값 한 쌍 */
export interface MetricValue {
  value: number;
  target: number;
  unit?: string;
}

/** 식단 결과의 영양 달성률 3종 */
export interface NutritionAchievement {
  calories: MetricValue;
  protein: MetricValue;
  /** 나트륨 목표 대비 */
  sodium: MetricValue;
}

/** 1인 1식 원가 */
export interface CostPerServing {
  /** 원 */
  value: number;
  /** 1인 1식 예산(원). 없으면 예산 대비 표기를 생략한다. */
  budget?: number | null;
}

export interface KpiRowProps {
  achievement?: NutritionAchievement | null;
  cost?: CostPerServing | null;
  /** 생성 중이면 true */
  loading?: boolean;
  /**
   * 'ring' : 확정 화면 — 원형 게이지 3개 + 1인 원가, 가로 4칸
   * 'bar'  : 검토 화면 우측 열 — 가로 바 3개 + 1인 원가, 카드 하나에 세로 스택
   */
  variant?: 'ring' | 'bar';
  style?: CSSProperties;
}

// 지표별 목표 성격: 열량=목표 ±10% 적정, 단백질=최소 기준(넘으면 달성), 나트륨=상한(넘으면 초과)
const ITEMS: readonly { key: keyof NutritionAchievement; label: string; barLabel: string; mode: GaugeMode }[] = [
  { key: 'calories', label: '열량', barLabel: '열량', mode: 'band' },
  { key: 'protein', label: '단백질', barLabel: '단백질 (최소 기준)', mode: 'min' },
  { key: 'sodium', label: '나트륨', barLabel: '나트륨 (상한 대비)', mode: 'max' },
];

const card: CSSProperties = {
  background: colors.bgContainer,
  border: `1px solid ${colors.border}`,
  borderRadius: radius.card,
  padding: 20,
};

const grid: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
  gap: 16,
};

function toPercent(m: MetricValue) {
  if (!Number.isFinite(m.value) || !Number.isFinite(m.target) || m.target <= 0) return 0;
  return (m.value / m.target) * 100;
}

function amount(m: MetricValue) {
  const f = (n: number) => (Math.round(n * 10) / 10).toLocaleString('ko-KR');
  return `${f(m.value)} / ${f(m.target)}${m.unit ? ` ${m.unit}` : ''}`;
}

function Card({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <div style={{ ...card, ...style }}>{children}</div>;
}

function Dash({ label }: { label: string }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 8,
        color: colors.textTertiary,
      }}
    >
      <span style={{ fontSize: 26, fontWeight: 600, lineHeight: 1 }}>–</span>
      <span style={{ fontSize: 12 }}>{label}</span>
    </div>
  );
}

function StatusText({ percent, mode = 'band' }: { percent: number; mode?: GaugeMode }) {
  const color = gaugeColor(percent, 10, mode);
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        fontSize: 11,
        fontWeight: 600,
        color,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: color,
          display: 'inline-block',
        }}
      />
      {gaugeStatus(percent, 10, mode)}
    </span>
  );
}

function CostValue({ cost, size = 30 }: { cost: CostPerServing; size?: number }) {
  const over = cost.budget != null && cost.value > cost.budget;
  const diff = cost.budget != null ? Math.round(cost.value - cost.budget) : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'inherit', gap: 4 }}>
      <span style={{ fontSize: 11, color: colors.textSecondary }}>1인 원가</span>
      <span
        style={{
          fontFamily: 'Quicksand, Pretendard, sans-serif',
          fontSize: size,
          fontWeight: 600,
          lineHeight: 1,
          letterSpacing: '-0.01em',
          color: over ? colors.error : colors.text,
        }}
      >
        {Math.round(cost.value).toLocaleString('ko-KR')}
        <span style={{ fontFamily: 'Pretendard, sans-serif', fontSize: size * 0.5, marginLeft: 2 }}>
          원
        </span>
      </span>
      {cost.budget != null && diff != null && (
        <span
          style={{
            fontSize: 11,
            color: over ? colors.error : colors.textTertiary,
            fontWeight: over ? 600 : 400,
          }}
        >
          예산 {Math.round(cost.budget).toLocaleString('ko-KR')}원{' '}
          {over ? `· ${diff.toLocaleString('ko-KR')}원 초과` : '내'}
        </span>
      )}
    </div>
  );
}

/**
 * 식단 결과의 영양 달성률 + 1인 원가.
 *
 * 순수 표시 컴포넌트다. API 호출·상태 관리를 하지 않으므로
 * 결과 화면에서 응답을 이 props 모양으로 넘겨주기만 하면 된다.
 */
export default function KpiRow({
  achievement,
  cost,
  loading = false,
  variant = 'ring',
  style,
}: KpiRowProps) {
  if (loading) {
    return variant === 'bar' ? (
      <Card style={style}>
        <Skeleton active paragraph={{ rows: 5 }} title={false} />
      </Card>
    ) : (
      <div style={{ ...grid, ...style }}>
        {[0, 1, 2, 3].map((i) => (
          <Card key={i} style={{ minHeight: 176 }}>
            <Skeleton active paragraph={{ rows: 2 }} title={false} />
          </Card>
        ))}
      </div>
    );
  }

  /* 검토 화면 우측 열 */
  if (variant === 'bar') {
    return (
      <Card style={style}>
        <div style={{ fontSize: 13, fontWeight: 600, color: colors.text, marginBottom: 16 }}>
          영양 달성률
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {ITEMS.map(({ key, label, barLabel, mode }) => {
            const m = achievement?.[key];
            if (!m) return <Dash key={key} label={label} />;
            return (
              <Gauge
                key={key}
                variant="bar"
                label={barLabel}
                value={toPercent(m)}
                caption={amount(m)}
                mode={mode}
              />
            );
          })}
        </div>

        <div style={{ marginTop: 18, paddingTop: 16, borderTop: `1px solid ${colors.border}` }}>
          {cost ? <CostValue cost={cost} size={24} /> : <Dash label="1인 원가" />}
        </div>
      </Card>
    );
  }

  /* 확정 화면 */
  return (
    <div style={{ ...grid, ...style }}>
      {ITEMS.map(({ key, label, mode }) => {
        const m = achievement?.[key];
        return (
          <Card
            key={key}
            style={{
              minHeight: 176,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
            }}
          >
            {m ? (
              <>
                <Gauge label={label} value={toPercent(m)} mode={mode} />
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    fontSize: 11,
                    color: colors.textTertiary,
                  }}
                >
                  <span>{amount(m)}</span>
                  <StatusText percent={toPercent(m)} mode={mode} />
                </div>
              </>
            ) : (
              <Dash label={label} />
            )}
          </Card>
        );
      })}
      <Card
        style={{
          minHeight: 176,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {cost ? <CostValue cost={cost} /> : <Dash label="1인 원가" />}
      </Card>
    </div>
  );
}