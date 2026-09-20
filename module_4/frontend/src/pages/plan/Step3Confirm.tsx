// src/pages/plan/Step3Confirm.tsx
// 식단 생성 3단계 · 확정 (시안 화면 6). 요약 + 다운로드 파일 선택 + CSV 내려받기.
import { useState } from 'react';
import { Card, Button, Input, Checkbox, message } from 'antd';
import { CheckCircleFilled, DownloadOutlined } from '@ant-design/icons';
import StepIndicator from './StepIndicator';
import KpiRow from '../../components/KpiRow';
import { MEAL_TABLE, type MealPlan, type MealKind } from '../../api/menu';

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7', line: '#EEF2F0',
  green: '#12A150', greenText: '#0B6B36', tint: '#E4F7EB', tintBg: '#F1FAF4',
  red: '#E5484D', redText: '#B42318',
};

function saveCsv(name: string, rows: (string | number)[][]) {
  const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// 식단표: 주차·요일·끼니별 메뉴 + 1인 열량·단백질
function tableRows(plan: MealPlan): (string | number)[][] {
  const rows: (string | number)[][] = [['주차', '날짜', '요일', '끼니', '메뉴', '열량(kcal)', '단백질(g)']];
  plan.weeks.forEach((wk) => wk.days.forEach((d) => d.cells.forEach((c) => {
    rows.push([wk.label, d.date, d.dow, MEAL_TABLE[c.kind as MealKind], c.items.map((i) => i.name).join(' '), c.kcal, c.protein]);
  })));
  return rows;
}

// 조리 지시서: 메뉴별 재료 투입량(총량)·조리 순서.
// menuRecipes(백엔드 recipe_ingredient_map 보강분)가 있으면 실데이터를, 없으면(mock/미보강 메뉴)
// 골격 문구로 저하한다.
function recipeRows(weeksSrc: MealPlan['weeks'], headcount: number, menuRecipes?: MealPlan['menuRecipes']): (string | number)[][] {
  const rows: (string | number)[][] = [['날짜', '끼니', '메뉴', '재료명', '투입량(g, 총량)', '조리순서']];
  weeksSrc.forEach((wk) => wk.days.forEach((d) => d.cells.forEach((c) => {
    c.items.forEach((it) => {
      const rec = menuRecipes?.[it.name];
      if (rec && rec.ingredients.length) {
        rec.ingredients.forEach((ing) => rows.push([
          d.date, MEAL_TABLE[c.kind as MealKind], it.name,
          ing.name, ing.amount ?? '', ing.step ?? '',
        ]));
      } else {
        rows.push([d.date, MEAL_TABLE[c.kind as MealKind], it.name, rec?.note || '(재료 연동 예정)', `1인분×${headcount}`, '(조리 순서 연동 예정)']);
      }
    });
  })));
  return rows;
}

const FileRow = ({ checked, onToggle, title, badge, desc, disabled }: { checked: boolean; onToggle?: () => void; title: string; badge?: string; desc: string; disabled?: boolean }) => (
  <div onClick={disabled ? undefined : onToggle}
    style={{ display: 'flex', alignItems: 'flex-start', gap: 12, border: `1px solid ${checked ? C.green : C.border}`, background: disabled ? '#F7FAF8' : checked ? C.tintBg : '#fff', borderRadius: 12, padding: '14px 16px', cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.7 : 1 }}>
    <Checkbox checked={checked} disabled={disabled} style={{ marginTop: 2 }} />
    <div style={{ flex: 1 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: disabled ? C.muted : C.text }}>{title}</span>
        {badge && <span style={{ fontSize: 11, fontWeight: 700, color: C.greenText, background: C.tint, borderRadius: 6, padding: '1px 6px' }}>{badge}</span>}
        {disabled && <span style={{ fontSize: 11, color: C.sub, background: C.line, borderRadius: 6, padding: '1px 6px', marginLeft: 'auto' }}>준비 중</span>}
      </div>
      <div style={{ marginTop: 3, fontSize: 12, color: C.sub }}>{desc}</div>
    </div>
  </div>
);

export default function Step3Confirm({ plan, onPrev, onSaveDraft }: {
  plan: MealPlan; onPrev: () => void; onSaveDraft: () => void;
}) {
  const [files, setFiles] = useState({ table: true, normal: true, alt: false });
  const [name, setName] = useState('9월 2-3주차 · 초등학생 중식');

  const first = plan.weeks[0]?.days[0]?.date ?? '';
  const lastWk = plan.weeks[plan.weeks.length - 1];
  const last = lastWk?.days[lastWk.days.length - 1]?.date ?? '';
  const mealsText = plan.meals.map((m) => MEAL_TABLE[m]).join('·');
  const allergyN = plan.alternatives.reduce((s, t) => s + t.count, 0);

  const confirm = () => {
    const base = (name.trim() || '식단') ;
    const jobs: (() => void)[] = [];
    if (files.table) jobs.push(() => saveCsv(`${base}_식단표.csv`, tableRows(plan)));
    if (files.normal) jobs.push(() => saveCsv(`${base}_일반식_조리지시서.csv`, recipeRows(plan.weeks, plan.headcount, plan.menuRecipes)));
    if (files.alt) jobs.push(() => saveCsv(`${base}_대체식_조리지시서.csv`, recipeRows(plan.alternatives.flatMap((t) => t.weeks), plan.headcount, plan.menuRecipes)));
    if (!jobs.length) { message.warning('내려받을 파일을 하나 이상 선택해 주세요'); return; }
    // 브라우저가 연속 다운로드를 막지 않도록 약간 간격을 둠
    jobs.forEach((run, i) => setTimeout(run, i * 350));
    message.success(`식단이 확정되고 CSV ${jobs.length}개를 내려받았어요`);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <StepIndicator current={3} />

      <div style={{ background: C.tintBg, border: `1px solid ${C.tint}`, borderRadius: 14, padding: '20px 24px', display: 'flex', alignItems: 'center', gap: 14 }}>
        <CheckCircleFilled style={{ fontSize: 30, color: C.green }} />
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, color: C.text }}>검토가 끝났어요</div>
          <div style={{ marginTop: 3, fontSize: 13, color: C.sub }}>확정하면 식단표와 조리 지시서를 CSV 파일로 내려받을 수 있어요</div>
        </div>
      </div>

      {/* 요약 */}
      <Card size="small" styles={{ body: { padding: 24 } }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          {[['대상', `${plan.conditionText.split(' · ')[0]} · ${plan.headcount}명`], ['기간 · 끼니', `${first}–${last} 평일 · ${mealsText}`], ['알레르기 그룹', `${plan.alternatives.length}그룹 · ${allergyN}명`]].map(([k, v]) => (
            <div key={k}><div style={{ fontSize: 12, color: C.sub }}>{k}</div><div style={{ marginTop: 4, fontSize: 15, fontWeight: 600, color: C.text }}>{v}</div></div>
          ))}
        </div>
      </Card>

      {/* 영양 달성률 + 1인 원가 (박미연 KpiRow) */}
      <KpiRow variant="ring" achievement={plan.achievement} cost={{ value: plan.costPerPerson, budget: plan.budgetPerPerson }} />
      <div style={{ textAlign: 'right', marginTop: -6, fontSize: 12, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>총 예상 식재료비 {plan.totalCost.toLocaleString()}원</div>

      {/* 내려받을 파일 */}
      <Card size="small" styles={{ body: { padding: 24 } }}>
        <div style={{ marginBottom: 14 }}><span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>내려받을 파일</span><span style={{ fontSize: 12, color: C.sub, marginLeft: 8 }}>CSV · 엑셀에서 바로 열 수 있어요</span></div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <FileRow checked={files.table} onToggle={() => setFiles((f) => ({ ...f, table: !f.table }))} title="식단표" badge="CSV" desc={`주차·요일·끼니별 메뉴와 1인 기준 열량·단백질이 들어간 게시용 표 · 평일 ${plan.totalDays}일`} />
          <FileRow checked={files.normal} onToggle={() => setFiles((f) => ({ ...f, normal: !f.normal }))} title="일반식 조리 지시서" badge="CSV" desc="확정된 식단의 메뉴별 재료 투입량(총량)과 조리 순서 · 열: 날짜/끼니/메뉴/재료명/투입량(g)/조리순서" />
          <FileRow checked={files.alt} onToggle={() => setFiles((f) => ({ ...f, alt: !f.alt }))} title="대체식 조리 지시서" badge="CSV" desc="알레르기 그룹 대체 메뉴의 재료 투입량과 조리 순서" />
          <FileRow checked={false} disabled title="PDF 인쇄본 · 발주 목록 · 로봇 조리 JSON" desc="추후 제공 예정이에요" />
        </div>
        <div style={{ marginTop: 20 }}>
          <div style={{ fontSize: 12, color: C.sub, marginBottom: 6 }}>식단 이름</div>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
      </Card>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Button onClick={onPrev}>이전</Button>
        <div style={{ flex: 1 }} />
        <Button onClick={onSaveDraft}>초안으로 저장</Button>
        <Button type="primary" icon={<DownloadOutlined />} onClick={confirm}>확정하고 CSV 내려받기</Button>
      </div>
    </div>
  );
}
