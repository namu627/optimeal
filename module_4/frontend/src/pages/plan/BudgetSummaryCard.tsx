// src/pages/plan/BudgetSummaryCard.tsx
// 이월(기간 총액 기준) 모드 총액 요약 카드 — KpiRow(박미연) 아래 별도 카드. KpiRow 내부는 건드리지 않는다.
// 수치는 칸 영양으로 매번 다시 계산(budgetSummary)하므로 교체·삭제 직후에도 맞다. 하루 단위 모드에서는 그리지 않는다.
import { Card, Tooltip } from 'antd';
import { budgetSummary, CARRYOVER_BAND, type MealPlan } from '../../api/menu';

const C = { sub: '#5D6B64', text: '#16211C', redText: '#B42318', amberText: '#8A5A00', slate: '#5B6B7F' };
const won = (n: number) => `${n.toLocaleString()}원`;

export default function BudgetSummaryCard({ plan }: { plan: MealPlan }) {
  const s = budgetSummary(plan);
  if (!s) return null;
  const over = s.headroom < 0;
  const lo = Math.round(plan.budgetPerPerson * CARRYOVER_BAND.low), hi = Math.round(plan.budgetPerPerson * CARRYOVER_BAND.high);
  const row = { fontSize: 13, color: C.sub, fontVariantNumeric: 'tabular-nums' as const, lineHeight: '22px' };
  return (
    <Card size="small">
      <Tooltip title={`예산 방식: 기간 총액 기준(이월). 총예산 = 1끼 ${won(plan.budgetPerPerson)} × ${plan.meals.length}끼 × ${plan.totalDays}일. 한 끼가 1끼 예산을 넘어도 기간 총액 안이면 허용돼요.`}>
        <div style={row}>
          기간 총 원가 <b style={{ color: over ? C.redText : C.text }}>{won(s.total)}</b> / 총예산 {won(s.budget)}
        </div>
      </Tooltip>
      <div style={row}>
        {over ? <span style={{ color: C.redText }}>초과 {won(-s.headroom)}</span> : <>여유 {won(s.headroom)}</>}
        {' · '}끼니 평균 {won(s.mealAvg)}
      </div>
      <div style={row}>
        {s.guardMin != null && s.guardMax != null && (
          <Tooltip title="모든 끼니의 1인 원가가 이 범위 안에 들어오도록 생성했어요(교체도 이 범위 안에서만 돼요)">
            <span>끼니 원가 범위 {s.guardMin.toLocaleString()}~{won(s.guardMax)} · </span>
          </Tooltip>
        )}
        <Tooltip title={`끼니 원가가 1끼 예산의 ${CARRYOVER_BAND.low * 100}~${CARRYOVER_BAND.high * 100}%(${lo.toLocaleString()}~${won(hi)}) 밖인 끼니 — 이월로 허용된 상태라 참고용이에요`}>
          <span style={{ color: s.outOfBand ? C.slate : C.sub }}>기준 이탈 {s.outOfBand}/{s.meals}끼(참고)</span>
        </Tooltip>
      </div>
      {s.guardRelaxed && (
        <div style={{ ...row, color: C.amberText, marginTop: 4 }}>원가 범위를 지킬 수 없어 범위 제한 없이 생성했어요</div>
      )}
    </Card>
  );
}
