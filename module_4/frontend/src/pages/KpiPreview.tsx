import type { ReactNode } from 'react';
import KpiRow from '../components/KpiRow';
import {
  mockAchievement,
  mockAchievementUnder,
  mockCost,
  mockCostOverBudget,
} from '../components/mockKpi';
import { colors } from '../theme';

/**
 * 게이지 확인용 임시 화면. 확인 끝나면 지워도 된다.
 */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ marginBottom: 32 }}>
      <h2 style={{ fontSize: 13, fontWeight: 600, color: colors.textSecondary, margin: '0 0 12px' }}>
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function KpiPreview() {
  return (
    <div style={{ padding: '18px 28px' }}>
      <h1 style={{ fontSize: 15, fontWeight: 600, color: colors.text, margin: '0 0 24px' }}>
        영양 달성률 게이지 프리뷰
      </h1>

      <Section title="확정 화면 (ring) — 97 / 104 / 112 · 예산 내">
        <KpiRow achievement={mockAchievement} cost={mockCost} />
      </Section>

      <Section title="부족 + 예산 초과">
        <KpiRow achievement={mockAchievementUnder} cost={mockCostOverBudget} />
      </Section>

      <Section title="검토 화면 우측 열 (bar)">
        <div style={{ maxWidth: 300 }}>
          <KpiRow achievement={mockAchievement} cost={mockCost} variant="bar" />
        </div>
      </Section>

      <Section title="생성 중 (loading)">
        <KpiRow loading />
      </Section>

      <Section title="결과 없음 (자리만 확보)">
        <KpiRow />
      </Section>
    </div>
  );
}