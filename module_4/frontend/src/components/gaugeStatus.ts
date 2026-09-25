// Gauge·KpiRow 공용 상태 판정. 컴포넌트 파일(Gauge.tsx)은 컴포넌트만 export 해야
// Vite fast refresh 가 동작하므로(react-refresh/only-export-components) 여기로 분리했다.
import { colors } from '../theme';

/**
 * 지표 성격 — 목표선(100%)을 어떻게 해석할지.
 *  'band' : 목표 ±tolerance 가 적정(열량). 기본값 = 기존 동작.
 *  'min'  : 목표는 최소 기준 — 이상이면 달성, 미만이면 부족(단백질). 초과는 경고가 아니다.
 *  'max'  : 목표는 상한 — 이하면 적정, 넘으면 초과(나트륨).
 */
export type GaugeMode = 'band' | 'min' | 'max';

/** 색은 항목이 아니라 상태가 결정한다 — band: 90% 미만 부족 / 90~110% 적정 / 110% 초과 초과 */
export function gaugeColor(value: number, tolerance = 10, mode: GaugeMode = 'band') {
  if (mode === 'min') return value >= 100 ? colors.primary : colors.warning;
  if (mode === 'max') return value > 100 ? colors.error : colors.primary;
  if (value < 100 - tolerance) return colors.warning;
  if (value > 100 + tolerance) return colors.error;
  return colors.primary;
}

export function gaugeStatus(value: number, tolerance = 10, mode: GaugeMode = 'band') {
  if (mode === 'min') return value >= 100 ? '달성' : '부족';
  if (mode === 'max') return value > 100 ? '초과' : '적정';
  if (value < 100 - tolerance) return '부족';
  if (value > 100 + tolerance) return '초과';
  return '적정';
}
