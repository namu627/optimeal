// src/pages/plan/Step3Confirm.tsx
// 식단 생성 3단계 · 확정 (시안 화면 6). 요약 + 다운로드 파일 선택 + CSV·PDF 내려받기.
import { useState } from 'react';
import { Card, Button, Input, Checkbox, App } from 'antd';
import { CheckCircleFilled, DownloadOutlined, ReadOutlined } from '@ant-design/icons';
import StepIndicator from './StepIndicator';
import RecipeDrawer from './RecipeDrawer';
import KpiRow from '../../components/KpiRow';
import BudgetSummaryCard from './BudgetSummaryCard';
import { MEAL_TABLE, planDateRange, planTargetLabel, type MealPlan } from '../../api/menu';
import { saveCsv, tableRows, recipeRows, altRecipeRows, downloadPlanPdf, describeExportError } from './planExport';

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7', line: '#EEF2F0',
  green: '#12A150', greenText: '#0B6B36', tint: '#E4F7EB', tintBg: '#F1FAF4',
  red: '#E5484D', redText: '#B42318',
};

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
  plan: MealPlan; onPrev: () => void;
  /** 초안 저장 — 입력한 식단 이름을 넘긴다. 실패 시 reject(버튼 로딩 해제용). */
  onSaveDraft: (name: string) => Promise<void>;
}) {
  const { message } = App.useApp();
  // pdfAlt: PDF 에 알레르기 그룹별 대체 메뉴 표(+ 조리 지시서 포함 시 대체식 조리 지시서). 그룹이 있으면 기본 켬.
  const hasAlt = plan.alternatives.length > 0;
  const [files, setFiles] = useState({ table: true, normal: true, alt: false, pdf: true, pdfRecipes: false, pdfAlt: hasAlt });
  const [exporting, setExporting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [recipeOpen, setRecipeOpen] = useState(false);
  const range = planDateRange(plan);
  const mealsText = plan.meals.map((m) => MEAL_TABLE[m]).join('·');
  // 기본 식단 이름은 실제 조건(기간·대상·끼니)에서 만든다. 예: '9/25–10/1 · 초등학생 중식'
  const [name, setName] = useState(() => `${range} · ${planTargetLabel(plan)} ${mealsText}`);
  const allergyN = plan.alternatives.reduce((s, t) => s + t.count, 0);

  const confirm = async () => {
    const base = (name.trim() || '식단') ;
    const jobs: { label: string; run: () => void | Promise<void> }[] = [];
    // PDF 를 맨 앞에 — 받은 뒤 새 탭으로 여는데(window.open), 클릭 직후여야 팝업 차단을 덜 받는다.
    if (files.pdf) jobs.push({ label: 'PDF', run: () => downloadPlanPdf(plan, base, { recipes: files.pdfRecipes, alternatives: files.pdfAlt }) });
    if (files.table) jobs.push({ label: '식단표 CSV', run: () => saveCsv(`${base}_식단표.csv`, tableRows(plan)) });
    // 조리 지시서 CSV 는 응답에 없는 메뉴(교체·대체식)의 레시피를 서버에서 조회한 뒤 만든다(PDF 와 같은 규칙).
    if (files.normal) jobs.push({ label: '일반식 조리 지시서 CSV', run: async () => saveCsv(`${base}_일반식_조리지시서.csv`, await recipeRows(plan, plan.weeks)) });
    if (files.alt) jobs.push({ label: '대체식 조리 지시서 CSV', run: async () => saveCsv(`${base}_대체식_조리지시서.csv`, await altRecipeRows(plan)) });
    if (!jobs.length) { message.warning('내려받을 파일을 하나 이상 선택해 주세요'); return; }
    setExporting(true);
    let ok = 0;
    // 차례로 실행 — 브라우저가 연속 다운로드를 막지 않도록 약간 간격을 둔다. PDF 는 서버 생성이라 기다린다.
    for (const { label, run } of jobs) {
      try { await run(); ok += 1; } catch (e) {
        console.error(`[확정] ${label} 내려받기 실패:`, e);
        message.error(`${label}를 만들지 못했어요 — ${await describeExportError(e)}`);
      }
      await new Promise((r) => setTimeout(r, 350));
    }
    setExporting(false);
    if (ok) message.success(`식단이 확정되고 파일 ${ok}개를 내려받았어요`);
  };
  const saveDraft = async () => {
    const n = name.trim();
    if (!n) { message.warning('식단 이름을 입력해 주세요'); return; }
    setSaving(true);
    try { await onSaveDraft(n); } catch { setSaving(false); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <StepIndicator current={3} />

      <div style={{ background: C.tintBg, border: `1px solid ${C.tint}`, borderRadius: 14, padding: '20px 24px', display: 'flex', alignItems: 'center', gap: 14 }}>
        <CheckCircleFilled style={{ fontSize: 30, color: C.green }} />
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, color: C.text }}>검토가 끝났어요</div>
          <div style={{ marginTop: 3, fontSize: 13, color: C.sub }}>확정하면 식단표와 조리 지시서를 CSV·PDF 파일로 내려받을 수 있어요</div>
        </div>
        <div style={{ flex: 1 }} />
        <Button icon={<ReadOutlined />} onClick={() => setRecipeOpen(true)}>레시피 보기</Button>
      </div>
      <RecipeDrawer plan={plan} open={recipeOpen} onClose={() => setRecipeOpen(false)} />

      {/* 요약 */}
      <Card size="small" styles={{ body: { padding: 24 } }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          {[['대상', `${planTargetLabel(plan)} · ${plan.headcount}명`], ['기간 · 끼니', `${range} 평일 · ${mealsText}`],['알레르기 그룹', `${plan.alternatives.length}그룹 · ${allergyN}명`]].map(([k, v]) => (
            <div key={k}><div style={{ fontSize: 12, color: C.sub }}>{k}</div><div style={{ marginTop: 4, fontSize: 15, fontWeight: 600, color: C.text }}>{v}</div></div>
          ))}
        </div>
      </Card>

      {/* 영양 달성률 + 1인 원가 (박미연 KpiRow) */}
      <KpiRow variant="ring" achievement={plan.achievement} cost={{ value: plan.costPerPerson, budget: plan.budgetPerPerson }} />
      <div style={{ textAlign: 'right', marginTop: -6, fontSize: 12, color: C.sub, fontVariantNumeric: 'tabular-nums' }}>총 예상 식재료비 {plan.totalCost.toLocaleString()}원</div>
      <BudgetSummaryCard plan={plan} />

      {/* 내려받을 파일 */}
      <Card size="small" styles={{ body: { padding: 24 } }}>
        <div style={{ marginBottom: 14 }}><span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>내려받을 파일</span><span style={{ fontSize: 12, color: C.sub, marginLeft: 8 }}>CSV는 엑셀에서, PDF는 인쇄·게시용</span></div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <FileRow checked={files.table} onToggle={() => setFiles((f) => ({ ...f, table: !f.table }))} title="식단표" badge="CSV" desc={`주차·요일·끼니별 메뉴와 1인 기준 열량·단백질이 들어간 게시용 표 · 평일 ${plan.totalDays}일`} />
          <FileRow checked={files.normal} onToggle={() => setFiles((f) => ({ ...f, normal: !f.normal }))} title="일반식 조리 지시서" badge="CSV" desc="확정된 식단의 메뉴별 재료 투입량(총량)과 조리 순서 · 열: 날짜/끼니/메뉴/재료명/투입량(g)/조리순서" />
          <FileRow checked={files.alt} onToggle={() => setFiles((f) => ({ ...f, alt: !f.alt }))} title="대체식 조리 지시서" badge="CSV" desc="알레르기 그룹별로 일반식과 달라진 대체 메뉴의 재료 투입량과 조리 순서 · 열: 그룹/날짜/끼니/메뉴/재료명/투입량(g)/기준/조리순서" />
          <FileRow checked={files.pdf} onToggle={() => setFiles((f) => ({ ...f, pdf: !f.pdf }))} title="PDF 인쇄본" badge="PDF" desc="상단 요약(대상·인원·기간·1인 원가) + 식단표 · 식단표 CSV와 같은 내용, 한글 폰트 포함" />
          {files.pdf && (
            <div style={{ marginTop: -4, paddingLeft: 42 }}>
              <Checkbox checked={files.pdfRecipes} onChange={(e) => setFiles((f) => ({ ...f, pdfRecipes: e.target.checked }))}>
                <span style={{ fontSize: 13, color: C.text }}>일반식 조리 지시서 포함</span>
                <span style={{ fontSize: 12, color: C.sub, marginLeft: 6 }}>메뉴별 재료 투입량(총량)과 조리 순서 원문</span>
              </Checkbox>
              {hasAlt && (
                <div style={{ marginTop: 6 }}>
                  <Checkbox checked={files.pdfAlt} onChange={(e) => setFiles((f) => ({ ...f, pdfAlt: e.target.checked }))}>
                    <span style={{ fontSize: 13, color: C.text }}>대체식 포함</span>
                    <span style={{ fontSize: 12, color: C.sub, marginLeft: 6 }}>
                      그룹별 대체 메뉴 표(바뀐 메뉴 표시){files.pdfRecipes ? ' + 대체식 조리 지시서' : ''}
                    </span>
                  </Checkbox>
                </div>
              )}
            </div>
          )}
          <FileRow checked={false} disabled title="발주 목록 · 로봇 조리 JSON" desc="추후 제공 예정이에요" />
        </div>
        <div style={{ marginTop: 20 }}>
          <div style={{ fontSize: 12, color: C.sub, marginBottom: 6 }}>식단 이름</div>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
      </Card>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Button onClick={onPrev}>이전</Button>
        <div style={{ flex: 1 }} />
        <Button onClick={saveDraft} loading={saving}>초안으로 저장</Button>
        <Button type="primary" icon={<DownloadOutlined />} onClick={confirm} loading={exporting}>확정하고 내려받기</Button>
      </div>
    </div>
  );
}
