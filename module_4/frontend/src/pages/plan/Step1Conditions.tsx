// src/pages/plan/Step1Conditions.tsx
// 식단 생성 1단계 · 조건 입력 (시안 화면 4 / 4-a 생성중 / 4-b INFEASIBLE)
import { useMemo, useState } from 'react';
import { Card, Select, InputNumber, Button, Checkbox, Alert } from 'antd';
import { PlusOutlined, DeleteOutlined, CloseOutlined, ArrowRightOutlined } from '@ant-design/icons';
import StepIndicator from './StepIndicator';
import { PROFILE_OPTIONS, ALLERGEN_POOL, type MenuGenerateRequest, type AllergyGroup } from '../../api/menu';

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7', line: '#EEF2F0',
  green: '#12A150', greenText: '#0B6B36', tint: '#E4F7EB', tintBorder: '#BFEACF', head: '#F7FAF8',
  red: '#E5484D', redText: '#B42318', redTint: '#FDECEC', redBorder: '#F8D0D1',
};
type GenState = 'idle' | 'loading' | 'infeasible';

// 위저드에서 뒤로 돌아왔을 때 조건을 그대로 복원하기 위한 폼 스냅샷
export interface Step1Form {
  profile: string;
  count: number;
  conds: Record<string, number | null>;
  days: number;
  meals: string[];
  kcal: number;
  sodium: number;
  budget: number;
  groups: AllergyGroup[];
}

const DEFAULT_FORM: Step1Form = {
  profile: 'elem_low_mix',
  count: 320,
  conds: { 고혈압: 18, 당뇨: 6 },
  days: 7,
  meals: ['점심'],
  kcal: 1750,
  sodium: 1300,
  budget: 4500,
  groups: [
    { label: '그룹 1', allergens: ['난류', '우유'], count: 3 },
    { label: '그룹 2', allergens: ['땅콩'], count: 1 },
  ],
};

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div><div style={{ fontSize: 12, color: C.sub, marginBottom: 6 }}>{label}</div>{children}</div>
);

export default function Step1Conditions({ genState, onGenerate, onCancel, initial, onFormChange }: {
  genState: GenState; onGenerate: (req: MenuGenerateRequest) => void; onCancel: () => void;
  initial?: Step1Form; onFormChange?: (f: Step1Form) => void;
}) {
  const init = initial ?? DEFAULT_FORM;
  const [profile, setProfile] = useState(init.profile);
  const [count, setCount] = useState(init.count);
  const [conds, setConds] = useState<Record<string, number | null>>(init.conds);
  const [days, setDays] = useState(init.days);
  const [meals, setMeals] = useState<string[]>(init.meals);
  const [kcal, setKcal] = useState(init.kcal);
  const [sodium, setSodium] = useState(init.sodium);
  const [budget, setBudget] = useState(init.budget);
  const [groups, setGroups] = useState<AllergyGroup[]>(init.groups);

  const age = useMemo(() => PROFILE_OPTIONS.find((p) => p.value === profile)?.age ?? '', [profile]);
  const totalAllergy = groups.reduce((s, g) => s + (g.count || 0), 0);

  const toggleCond = (k: string) =>
    setConds((prev) => (k in prev ? (() => { const n = { ...prev }; delete n[k]; return n; })() : { ...prev, [k]: null }));
  // 항상 아침→점심→저녁 순으로 유지 (클릭 순서와 무관하게 식단표 행 순서가 흔들리지 않도록)
  const MEAL_ORDER = ['아침', '점심', '저녁'];
  const toggleMeal = (m: string) =>
    setMeals((p) => (p.includes(m) ? p.filter((x) => x !== m) : MEAL_ORDER.filter((x) => p.includes(x) || x === m)));

  const submit = () => {
    onFormChange?.({ profile, count, conds, days, meals, kcal, sodium, budget, groups });
    onGenerate({
      profile_key: profile, serving_count: count, days, meals,
      target_kcal_per_day: kcal, sodium_max_mg_per_day: sodium, budget_limit_per_person: budget,
      conditions: Object.keys(conds), with_alternatives: true,
      allergy_groups: groups.filter((g) => g.allergens.length),
    });
  };

  const CondRow = ({ name }: { name: string }) => {
    const on = name in conds;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 44, padding: '0 12px', borderRadius: 10, border: `1px solid ${on ? C.green : C.border}`, background: on ? C.tint : '#fff', cursor: 'pointer' }} onClick={() => toggleCond(name)}>
        <Checkbox checked={on} />
        <span style={{ flex: 1, fontSize: 13, color: on ? C.text : C.sub }}>{name}</span>
        <div onClick={(e) => e.stopPropagation()}>
          <InputNumber size="small" disabled={!on} value={conds[name] ?? undefined} min={0} onChange={(v) => setConds((p) => ({ ...p, [name]: Number(v) || 0 }))} style={{ width: 78 }} suffix="명" />
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, position: 'relative' }}>
      <StepIndicator current={1} />

      {genState === 'infeasible' && (
        <Alert type="error" showIcon
          message="조건을 만족하는 식단을 찾지 못했습니다"
          description="입력한 조건이 서로 충돌해 해를 찾지 못했습니다. 예산·나트륨 상한·알레르기 제외 범위·끼니 구성 같은 조건을 조정하면 다시 생성할 수 있어요."
          action={<Button danger onClick={submit}>조건 수정하기</Button>}
        />
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'start' }}>
        {/* 대상 */}
        <Card size="small" title="대상">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <Field label="대상 프로파일">
              <Select value={profile} onChange={setProfile} style={{ width: '100%' }}
                options={PROFILE_OPTIONS.map((p) => ({ value: p.value, label: p.label }))} />
            </Field>
            <Field label="인원수">
              <InputNumber value={count} min={1} onChange={(v) => setCount(Number(v) || 0)} suffix="명" style={{ width: '100%' }} />
            </Field>
          </div>
          <Field label="연령대 (자동)"><div style={{ height: 40, border: `1px solid ${C.line}`, background: C.head, borderRadius: 10, display: 'flex', alignItems: 'center', padding: '0 12px', fontSize: 13, color: C.sub, marginTop: 14 }}>{age}</div></Field>
          <div style={{ marginTop: 14, fontSize: 12, color: C.sub, marginBottom: 8 }}>기저질환 (다중 선택)</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <CondRow name="고혈압" /><CondRow name="당뇨" /><CondRow name="신장질환" />
          </div>
        </Card>

        {/* 생성 조건 */}
        <Card size="small" title="생성 조건">
          <Field label="기간">
            <div style={{ display: 'flex', gap: 10 }}>
              {[7, 31].map((d) => (
                <div key={d} onClick={() => setDays(d)} style={{ flex: 1, height: 40, borderRadius: 10, border: `1px solid ${days === d ? C.green : C.border}`, background: days === d ? C.tint : '#fff', color: days === d ? C.greenText : C.sub, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, cursor: 'pointer', fontWeight: days === d ? 600 : 400 }}>
                  <span style={{ width: 14, height: 14, borderRadius: 7, border: `${days === d ? 4 : 1}px solid ${days === d ? C.green : C.border}`, background: '#fff' }} />{d}일
                </div>
              ))}
            </div>
          </Field>
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 12, color: C.sub, marginBottom: 6 }}>끼니</div>
            <div style={{ display: 'flex', gap: 10 }}>
              {['아침', '점심', '저녁'].map((m) => {
                const on = meals.includes(m);
                return (
                  <div key={m} onClick={() => toggleMeal(m)} style={{ flex: 1, height: 40, borderRadius: 10, border: `1px solid ${on ? C.green : C.border}`, background: on ? C.tint : '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, cursor: 'pointer' }}>
                    <Checkbox checked={on} /><span style={{ fontSize: 13, fontWeight: on ? 600 : 400, color: on ? C.text : C.sub }}>{m}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <Field label="1일 열량 목표"><InputNumber value={kcal} onChange={(v) => setKcal(Number(v) || 0)} suffix="kcal" style={{ width: '100%' }} /></Field>
            <Field label="나트륨 상한"><InputNumber value={sodium} onChange={(v) => setSodium(Number(v) || 0)} suffix="mg" style={{ width: '100%' }} /></Field>
          </div>
          <div style={{ marginTop: 6, marginBottom: 14, fontSize: 12, color: C.muted }}>프로파일 기준 자동 채움</div>
          <Field label="1인 1식 예산"><InputNumber value={budget} onChange={(v) => setBudget(Number(v) || 0)} suffix="원" style={{ width: 200 }} /></Field>
        </Card>
      </div>

      {/* 알레르기 그룹 */}
      <Card size="small">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <span style={{ fontSize: 15, fontWeight: 600, color: C.text }}>알레르기 그룹</span>
          <span style={{ fontSize: 12, color: C.sub, background: C.line, borderRadius: 8, padding: '2px 8px' }}>{groups.length}그룹 · {totalAllergy}명</span>
          <span style={{ fontSize: 12, color: C.muted }}>{ALLERGEN_POOL.join(' · ')} 중 선택</span>
          <div style={{ flex: 1 }} />
          <Button size="small" icon={<PlusOutlined />} onClick={() => setGroups((g) => [...g, { label: `그룹 ${g.length + 1}`, allergens: [], count: 0 }])}>그룹 추가</Button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {groups.map((g, gi) => (
            <div key={gi} style={{ display: 'flex', alignItems: 'center', gap: 12, border: `1px solid ${C.line}`, borderRadius: 10, padding: '8px 12px' }}>
              <span style={{ fontSize: 12, color: C.sub, width: 44 }}>{g.label}</span>
              <div style={{ flex: 1, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                {g.allergens.map((a) => (
                  <span key={a} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 13, color: C.greenText, background: C.tint, borderRadius: 8, padding: '3px 8px' }}>
                    {a}<CloseOutlined style={{ fontSize: 9, cursor: 'pointer' }} onClick={() => setGroups((gs) => gs.map((x, i) => i === gi ? { ...x, allergens: x.allergens.filter((y) => y !== a) } : x))} />
                  </span>
                ))}
                <Select<string> size="small" variant="borderless" placeholder="알레르기 추가…" style={{ minWidth: 110 }}
                  options={ALLERGEN_POOL.filter((a) => !g.allergens.includes(a)).map((a) => ({ value: a, label: a }))}
                  onChange={(a) => setGroups((gs) => gs.map((x, i) => i === gi ? { ...x, allergens: [...x.allergens, a] } : x))} />
              </div>
              <InputNumber size="small" value={g.count} min={0} onChange={(v) => setGroups((gs) => gs.map((x, i) => i === gi ? { ...x, count: Number(v) || 0 } : x))} suffix="명" style={{ width: 84 }} />
              <DeleteOutlined style={{ color: C.muted, cursor: 'pointer' }} onClick={() => setGroups((gs) => gs.filter((_, i) => i !== gi))} />
            </div>
          ))}
        </div>
      </Card>

      <div style={{ display: 'flex', alignItems: 'center' }}>
        <Button onClick={onCancel}>취소</Button>
        <div style={{ flex: 1 }} />
        <Button type="primary" onClick={submit}>다음: 식단 생성 <ArrowRightOutlined /></Button>
      </div>

      {/* 생성 중 오버레이 */}
      {genState === 'loading' && (
        <div style={{ position: 'absolute', inset: 0, background: 'rgba(247,250,248,0.72)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10 }}>
          <div style={{ width: 380, background: '#fff', border: `1px solid ${C.border}`, borderRadius: 14, padding: 28, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, boxShadow: '0 16px 40px rgba(22,33,28,0.12)' }}>
            <div style={{ width: 26, height: 26, borderRadius: 13, border: `2.5px solid ${C.tint}`, borderTopColor: C.green, animation: 'spin 0.9s linear infinite' }} />
            <div style={{ fontSize: 16, fontWeight: 600, color: C.text }}>식단을 생성하고 있습니다</div>
            <div style={{ fontSize: 12, color: C.sub }}>보통 30~50초 · 제약조건 최적화 중</div>
            <div style={{ width: '100%', height: 4, borderRadius: 2, background: C.line, overflow: 'hidden', marginTop: 4 }}>
              <div style={{ width: '45%', height: '100%', background: C.green }} />
            </div>
            <div style={{ fontSize: 12, color: C.muted }}>창을 닫아도 생성은 계속됩니다</div>
          </div>
          <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
        </div>
      )}
    </div>
  );
}
