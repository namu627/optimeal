import { colors } from '../theme';

type Props = {
  value: number;      // 달성률 %
  label: string;      // 항목 이름
  size?: number;
  thickness?: number;
  tolerance?: number; // 적정 범위 (기본 ±10%)
};

export default function Gauge({
  value,
  label,
  size = 96,
  thickness = 9,
  tolerance = 10,
}: Props) {
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const ratio = Math.min(value, 100) / 100;

  const color =
    value < 100 - tolerance
      ? colors.warning
      : value > 100 + tolerance
      ? colors.error
      : colors.primary;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
      <div style={{ position: 'relative', width: size, height: size }}>
        <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={colors.gaugeTrack}
            strokeWidth={thickness}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={thickness}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - ratio)}
          />
        </svg>
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: size * 0.24,
            fontWeight: 600,
            color: colors.text,
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {value}
          <span style={{ fontSize: size * 0.14, marginLeft: 1 }}>%</span>
        </div>
      </div>
      <span style={{ fontSize: 12, color: colors.textSecondary }}>{label}</span>
    </div>
  );
}