import { colors } from '../theme';
import { gaugeColor, gaugeStatus, type GaugeMode } from './gaugeStatus';

/** 12시 100% 목표선 두께 (시안 00a) */
const TICK_WIDTH = 1.2;

/** 가운데 숫자. 최소 기준을 넘긴 값(예: 250%)은 퍼센트 대신 '달성'으로 보여준다 — 실제값은 캡션에 있다. */
function gaugeLabel(value: number, mode: GaugeMode): { text: string; percent: boolean } {
  if (mode === 'min' && value > 100) return { text: '달성', percent: false };
  return { text: String(Math.round(value)), percent: true };
}

type Props = {
  /** 목표 대비 달성률(%) */
  value: number;
  label: string;
  size?: number;
  thickness?: number;
  tolerance?: number;
  /** 'ring'(기본) | 'bar' — 좁은 열(검토 화면 우측)에서는 bar */
  variant?: 'ring' | 'bar';
  /** bar 아래 보조 문구. 예: '757 / 780 kcal' */
  caption?: string;
  /** 지표 성격(기본 'band' = 기존 동작). 채움은 어느 모드든 100%에서 멈춘다. */
  mode?: GaugeMode;
};

export default function Gauge(props: Props) {
  return props.variant === 'bar' ? <GaugeBar {...props} /> : <GaugeRing {...props} />;
}

function GaugeRing({ value, label, size = 112, thickness = 7.5, tolerance = 10, mode = 'band' }: Props) {
  const c = size / 2;
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const ratio = Math.max(0, Math.min(value, 100)) / 100;
  const color = gaugeColor(value, tolerance, mode);
  const center = gaugeLabel(value, mode);

  return (
    <div
      style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}
      role="img"
      aria-label={`${label} ${Math.round(value)}퍼센트 · ${gaugeStatus(value, tolerance, mode)}`}
    >
      <div style={{ position: 'relative', width: size, height: size }}>
        <svg width={size} height={size} aria-hidden="true">
          <circle
            cx={c}
            cy={c}
            r={radius}
            fill="none"
            stroke={colors.gaugeTrack}
            strokeWidth={thickness}
          />
          <circle
            cx={c}
            cy={c}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={thickness}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - ratio)}
            transform={`rotate(-90 ${c} ${c})`}
            style={{ transition: 'stroke-dashoffset 320ms ease, stroke 200ms ease' }}
          />
          {/* 12시 눈금 = 100% 목표선 */}
          <line
            x1={c}
            y1={c - radius - thickness / 2 - 1}
            x2={c}
            y2={c - radius + thickness / 2 + 1}
            stroke={colors.textTertiary}
            strokeWidth={TICK_WIDTH}
            opacity={0.75}
          />
          {/* 100% 를 넘으면 링을 다 채운 뒤 끝에 점 — 최소 기준(min)은 넘는 게 정상이라 표시하지 않음 */}
          {value > 100 && mode !== 'min' && (
            <circle
              cx={c}
              cy={c - radius}
              r={thickness * 0.46}
              fill={color}
              stroke={colors.bgContainer}
              strokeWidth={2}
            />
          )}
        </svg>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: 'Quicksand, Pretendard, sans-serif',
            fontSize: size * 0.27,
            fontWeight: 600,
            letterSpacing: '-0.01em',
            color: colors.text,
          }}
        >
          {center.percent ? (
            <>
              {center.text}
              <span style={{ fontSize: size * 0.16, marginLeft: 1 }}>%</span>
            </>
          ) : (
            <span style={{ fontFamily: 'Pretendard, sans-serif', fontSize: size * 0.2, color }}>{center.text}</span>
          )}
        </div>
      </div>
      <span style={{ fontSize: 12, color: colors.textSecondary }}>{label}</span>
    </div>
  );
}

function GaugeBar({ value, label, thickness = 8, tolerance = 10, caption, mode = 'band' }: Props) {
  const ratio = Math.max(0, Math.min(value, 100)) / 100;
  const color = gaugeColor(value, tolerance, mode);
  const center = gaugeLabel(value, mode);

  return (
    <div
      style={{ width: '100%' }}
      role="img"
      aria-label={`${label} ${Math.round(value)}퍼센트 · ${gaugeStatus(value, tolerance, mode)}`}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 8,
          marginBottom: 6,
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{ fontSize: 12, color: colors.textSecondary }}>{label}</span>
        <span
          style={{
            fontFamily: 'Quicksand, Pretendard, sans-serif',
            fontSize: 14,
            fontWeight: 600,
            color: center.percent ? colors.text : color,
          }}
        >
          {center.percent ? `${center.text}%` : center.text}
        </span>
      </div>

      <div
        style={{
          position: 'relative',
          height: thickness,
          borderRadius: thickness / 2,
          background: colors.gaugeTrack,
        }}
      >
        <div
          style={{
            width: `${ratio * 100}%`,
            minWidth: ratio > 0 ? thickness : 0,
            height: '100%',
            borderRadius: thickness / 2,
            background: color,
            transition: 'width 320ms ease, background 200ms ease',
          }}
        />
        {/* 오른쪽 끝 = 100% 목표선 */}
        <div
          style={{
            position: 'absolute',
            top: -1,
            right: 0,
            width: TICK_WIDTH,
            height: thickness + 2,
            background: colors.textTertiary,
            opacity: 0.75,
          }}
        />
        {value > 100 && mode !== 'min' && (
          <div
            style={{
              position: 'absolute',
              top: '50%',
              right: -thickness * 0.24,
              transform: 'translateY(-50%)',
              width: thickness * 0.92,
              height: thickness * 0.92,
              borderRadius: '50%',
              background: color,
              boxShadow: `0 0 0 2px ${colors.bgContainer}`,
            }}
          />
        )}
      </div>

      {caption && (
        <div
          style={{ marginTop: 6, fontSize: 11, color: colors.textTertiary, whiteSpace: 'nowrap' }}
        >
          {caption}
        </div>
      )}
    </div>
  );
}