// src/pages/plan/Step1Conditions.tsx
// 식단 생성 1단계 · 조건 입력 (시안 화면 4 / 4-a 생성중 / 4-b INFEASIBLE)
import { useEffect, useState, type ReactNode } from 'react';
import { Card, Select, InputNumber, Button, Checkbox, Alert, Segmented, Tooltip } from 'antd';
import { PlusOutlined, DeleteOutlined, CloseOutlined, ArrowRightOutlined } from '@ant-design/icons';
import StepIndicator from './StepIndicator';
import {
  PROFILE_OPTIONS, ALLERGEN_POOL, BUDGET_MODE_LABEL, listProfiles,
  type MenuGenerateRequest, type AllergyGroup, type MenuProfile, type BudgetMode,
  type GenerateError, type GenerateErrorKind,
} from '../../api/menu';

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7', line: '#EEF2F0',
  green: '#12A150', greenText: '#0B6B36', tint: '#E4F7EB', tintBorder: '#BFEACF', head: '#F7FAF8',
  red: '#E5484D', redText: '#B42318', redTint: '#FDECEC', redBorder: '#F8D0D1',
};
type GenState = 'idle' | 'loading' | 'infeasible' | 'timeout' | 'error';

// 생성 요청 실패 화면 — 원인별 제목·조치. 목업으로 대체하지 않는다(PlanCreate 참고).
const GEN_ERROR_TEXT: Record<GenerateErrorKind, { title: string; action: ReactNode }> = {
  network: {
    title: '백엔드에 연결할 수 없어요',
    action: <>백엔드 서버(uvicorn, 포트 8000)가 켜져 있는지 확인한 뒤 다시 시도해 주세요.</>,
  },
  db: {
    title: '영양성분 DB에 연결할 수 없어요',
    action: <>DB 컨테이너가 꺼져 있을 수 있어요. 터미널에서 <code>docker start optimeal_db</code> 를 실행한 뒤 다시 시도해 주세요.</>,
  },
  server: {
    title: '서버 오류로 식단을 만들지 못했어요',
    action: <>잠시 후 다시 시도해 주세요. 계속되면 백엔드 로그를 확인해 주세요.</>,
  },
};

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
  /** 예산 방식. 이 필드가 생기기 전 스냅샷에는 없다 → 기본값(carryover). */
  budgetMode?: BudgetMode;
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
  budgetMode: 'carryover',
  groups: [
    { label: '그룹 1', allergens: ['난류', '우유'], count: 3 },
    { label: '그룹 2', allergens: ['땅콩'], count: 1 },
  ],
};

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div><div style={{ fontSize: 12, color: C.sub, marginBottom: 6 }}>{label}</div>{children}</div>
);

// 기저질환 한 줄(체크 + 인원수). 렌더마다 새로 만들어지지 않도록 컴포넌트 밖에 둔다.
function CondRow({ name, on, count, onToggle, onCount }: {
  name: string; on: boolean; count: number | null | undefined;
  onToggle: (name: string) => void; onCount: (name: string, n: number) => void;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, height: 44, padding: '0 12px', borderRadius: 10, border: `1px solid ${on ? C.green : C.border}`, background: on ? C.tint : '#fff', cursor: 'pointer' }} onClick={() => onToggle(name)}>
      <Checkbox checked={on} />
      <span style={{ flex: 1, fontSize: 13, color: on ? C.text : C.sub }}>{name}</span>
      <div onClick={(e) => e.stopPropagation()}>
        <InputNumber size="small" disabled={!on} value={count ?? undefined} min={0} onChange={(v) => onCount(name, Number(v) || 0)} style={{ width: 78 }} suffix="명" />
      </div>
    </div>
  );
}

export default function Step1Conditions({ genState, genError, onGenerate, onCancel, initial, onFormChange }: {
  genState: GenState; genError?: GenerateError | null; onGenerate: (req: MenuGenerateRequest) => void; onCancel: () => void;
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
  const [budgetMode, setBudgetMode] = useState<BudgetMode>(init.budgetMode ?? 'carryover');
  const [groups, setGroups] = useState<AllergyGroup[]>(init.groups);
  // GET /api/menu/profiles — 프로파일별 영양 기준. null=불러오는 중, 'failed'=실패(수동 입력 허용)
  const [profiles, setProfiles] = useState<Record<string, MenuProfile> | null | 'failed'>(null);

  useEffect(() => {
    let alive = true;
    listProfiles()
      .then((list) => { if (alive) setProfiles(Object.fromEntries(list.map((p) => [p.profile_key, p]))); })
      .catch((e) => { console.warn('[식단생성] 프로파일 기준값 조회 실패 → 수동 입력:', e); if (alive) setProfiles('failed'); });
    return () => { alive = false; };
  }, []);

  // 프로파일을 불러왔으면 열량·나트륨은 프로파일 1일 기준값으로 고정(입력칸 잠금).
  // 백엔드는 profile_key 를 받으면 이 값을 끼니 수에 맞게 다시 산출하므로, 실제 적용값은 결과 화면 달성률에 나온다.
  const prof = profiles && profiles !== 'failed' ? profiles[profile] : undefined;
  const locked = !!prof;
  const kcalValue = prof ? prof.daily_kcal : kcal;
  const sodiumValue = prof?.sodium_cdrr_mg ?? sodium;

  const age = prof
    ? `${prof.group_name} · ${prof.age_band} · ${prof.source}`
    : PROFILE_OPTIONS.find((p) => p.value === profile)?.age ?? '';
  const targetHint = locked
    ? '프로파일 기준(자동) · 1일 기준값 — 실제 적용값은 끼니 수에 맞춰 조정되어 결과 화면 달성률에 표시돼요'
    : profiles === null ? '프로파일 기준값을 불러오는 중…' : '프로파일 기준값을 불러오지 못했어요 — 직접 입력';
  const totalAllergy = groups.reduce((s, g) => s + (g.count || 0), 0);

  const toggleCond = (k: string) =>
    setConds((prev) => (k in prev ? (() => { const n = { ...prev }; delete n[k]; return n; })() : { ...prev, [k]: null }));
  // 항상 아침→점심→저녁 순으로 유지 (클릭 순서와 무관하게 식단표 행 순서가 흔들리지 않도록)
  const MEAL_ORDER = ['아침', '점심', '저녁'];
  // 31일에 고를 수 있는 끼니 수. 2026-10-02 화면 기본 조건 측정(총 한도 80초, 힌트 몫 0.85·presolve 끔 — 백엔드 menu.py):
  //   점심 FEASIBLE ~50초, 2식 세 조합 각 3/3 FEASIBLE 54~63초, 3식 3/3 FEASIBLE 이지만 77.7~79.2초(여유 1초 안팎)라
  //   PC 부하에 시간 초과가 날 수 있어 2식까지만 허용한다(31일에서 세 번째 끼니가 잠기고, 전송 때도 잘린다).
  //   (2026-09-30 에는 3식이 2/2 UNKNOWN 이라 점심만 허용했었다.)
  const LONG_DAYS = 31;
  const LONG_MAX_MEALS = 2;
  const LONG_BLOCK_TEXT = `31일은 ${LONG_MAX_MEALS}끼까지 생성할 수 있어요(3식은 현재 시간 안에 안정적으로 생성되지 않음)`;
  // 31일에서 잠긴 끼니를 누르거나, 31일로 바꾸며 끼니가 잘렸을 때 위 안내를 띄운다.
  const [longBlocked, setLongBlocked] = useState(false);
  // 개수를 넘기면 점심 → 저녁 → 아침 순으로 남긴다. 결과는 아침→점심→저녁 순.
  const trimLong = (p: string[]) => {
    const keep = ['점심', '저녁', '아침'].filter((x) => p.includes(x)).slice(0, LONG_MAX_MEALS);
    return MEAL_ORDER.filter((x) => keep.includes(x));
  };
  const mealLocked = (m: string) => days === LONG_DAYS && !meals.includes(m) && meals.length >= LONG_MAX_MEALS;
  const toggleMeal = (m: string) => {
    if (mealLocked(m)) { setLongBlocked(true); return; }
    setLongBlocked(false);
    setMeals((p) => (p.includes(m) ? p.filter((x) => x !== m) : MEAL_ORDER.filter((x) => p.includes(x) || x === m)));
  };
  // 기간을 31일로 바꾸면 끼니를 LONG_MAX_MEALS 개까지만 남긴다. 7일·1일로 돌아가면 다시 고를 수 있다(자동으로 켜지는 않음).
  const pickDays = (d: number) => {
    setDays(d);
    setLongBlocked(d === LONG_DAYS && meals.length > LONG_MAX_MEALS);
    if (d === LONG_DAYS) setMeals(trimLong);
  };

  // over: 시간 초과 안내의 '기간 줄이기'·'하루 단위로 생성'이 바꾼 값을 바로 반영해 보낸다(상태 갱신을 기다리지 않음).
  const submit = (over: { days?: number; budgetMode?: BudgetMode } = {}) => {
    if (genState === 'loading') return; // 중복 제출 방지
    const d = over.days ?? days, bm = over.budgetMode ?? budgetMode;
    // 이전 폼 스냅샷이 31일에 허용보다 많은 끼니여도 LONG_MAX_MEALS 개까지만 보낸다.
    const sendMeals = d === LONG_DAYS ? trimLong(meals) : meals;
    if (over.days != null) setDays(over.days);
    if (over.budgetMode) setBudgetMode(over.budgetMode);
    onFormChange?.({ profile, count, conds, days: d, meals: sendMeals, kcal: kcalValue, sodium: sodiumValue, budget, budgetMode: bm, groups });
    onGenerate({
      profile_key: profile, serving_count: count, days: d, meals: sendMeals,
      target_kcal_per_day: kcalValue, sodium_max_mg_per_day: sodiumValue, budget_limit_per_person: budget,
      budget_mode: bm,
      conditions: Object.keys(conds), with_alternatives: true,
      allergy_groups: groups.filter((g) => g.allergens.length),
    });
  };
  const shorterDays = days > 7 ? 7 : days > 1 ? 1 : null;

  const setCondCount = (name: string, n: number) => setConds((p) => ({ ...p, [name]: n }));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, position: 'relative' }}>
      <StepIndicator current={1} />

      {genState === 'infeasible' && (
        <Alert type="error" showIcon
          message="조건을 만족하는 식단을 찾지 못했습니다"
          description="입력한 조건이 서로 충돌해 해를 찾지 못했습니다. 예산·나트륨 상한·알레르기 제외 범위·끼니 구성 같은 조건을 조정하면 다시 생성할 수 있어요."
          action={<Button danger onClick={() => submit()}>조건 수정하기</Button>}
        />
      )}
      {genState === 'error' && genError && (
        <Alert type="error" showIcon
          message={`식단을 생성하지 못했어요 — ${GEN_ERROR_TEXT[genError.kind].title}`}
          description={
            <div>
              <div>{GEN_ERROR_TEXT[genError.kind].action}</div>
              <div style={{ marginTop: 4, fontSize: 12, color: C.sub }}>원인: {genError.detail}</div>
            </div>
          }
          action={<Button danger type="primary" onClick={() => submit()}>다시 시도</Button>}
        />
      )}
      {genState === 'timeout' && (
        <Alert type="warning" showIcon
          message="시간 안에 식단을 찾지 못했어요"
          description={`조건이 충돌한 건 아니에요. 제한 시간 안에 조건을 모두 만족하는 식단을 찾지 못했어요 — 같은 조건으로 다시 시도하면 찾을 수 있어요.${
            shorterDays ? ` 기간을 ${shorterDays}일로 줄이거나` : ''}${budgetMode === 'carryover' ? ' 예산을 하루 단위로 바꾸면' : ''} 더 빨리 찾을 수 있어요.`}
          action={
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <Button size="small" type="primary" onClick={() => submit()}>다시 시도</Button>
              {shorterDays && <Button size="small" onClick={() => submit({ days: shorterDays })}>기간 {shorterDays}일로 줄이기</Button>}
              {budgetMode === 'carryover' && <Button size="small" onClick={() => submit({ budgetMode: 'day' })}>하루 단위로 생성</Button>}
            </div>
          }
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
            {['고혈압', '당뇨', '신장질환'].map((name) => (
              <CondRow key={name} name={name} on={name in conds} count={conds[name]} onToggle={toggleCond} onCount={setCondCount} />
            ))}
          </div>
        </Card>

        {/* 생성 조건 */}
        <Card size="small" title="생성 조건">
          <Field label="기간">
            <div style={{ display: 'flex', gap: 10 }}>
              {[1, 7, 31].map((d) => (
                <div key={d} onClick={() => pickDays(d)} style={{ flex: 1, height: 40, borderRadius: 10, border: `1px solid ${days === d ? C.green : C.border}`, background: days === d ? C.tint : '#fff', color: days === d ? C.greenText : C.sub, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, cursor: 'pointer', fontWeight: days === d ? 600 : 400 }}>
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
                const locked = mealLocked(m);
                const box = (
                  <div key={m} onClick={() => toggleMeal(m)} aria-disabled={locked} style={{ flex: 1, height: 40, borderRadius: 10, border: `1px solid ${on ? C.green : C.border}`, background: locked ? C.head : on ? C.tint : '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, cursor: locked ? 'not-allowed' : 'pointer', opacity: locked ? 0.55 : 1 }}>
                    <Checkbox checked={on} disabled={locked} /><span style={{ fontSize: 13, fontWeight: on ? 600 : 400, color: on ? C.text : C.sub }}>{m}</span>
                  </div>
                );
                return locked ? <Tooltip key={m} title={LONG_BLOCK_TEXT}>{box}</Tooltip> : box;
              })}
            </div>
          </div>
          {days === LONG_DAYS && longBlocked && (
            <Alert type="warning" showIcon style={{ marginTop: 10 }} title={LONG_BLOCK_TEXT} />
          )}
          {days === LONG_DAYS && meals.length >= 2 && (
            <Alert type="info" showIcon style={{ marginTop: 10 }} title="31일 2식은 생성에 1분 가까이 걸려요" />
          )}
          <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <Field label="1일 열량 목표"><InputNumber value={kcalValue} disabled={locked} onChange={(v) => setKcal(Number(v) || 0)} suffix="kcal" style={{ width: '100%' }} /></Field>
            <Field label="1일 나트륨 상한"><InputNumber value={sodiumValue} disabled={locked} onChange={(v) => setSodium(Number(v) || 0)} suffix="mg" style={{ width: '100%' }} /></Field>
          </div>
          <div style={{ marginTop: 6, marginBottom: 14, fontSize: 12, color: C.muted }}>{targetHint}</div>
          <Field label="1인 1식 예산"><InputNumber value={budget} onChange={(v) => setBudget(Number(v) || 0)} suffix="원" style={{ width: 200 }} /></Field>
          <div style={{ marginTop: 14 }}>
            <Field label="예산 방식">
              <Segmented<BudgetMode> value={budgetMode} onChange={setBudgetMode}
                options={(['carryover', 'day'] as const).map((m) => ({ value: m, label: BUDGET_MODE_LABEL[m] }))} />
            </Field>
            <div style={{ marginTop: 6, fontSize: 12, color: C.muted }}>
              {budgetMode === 'carryover'
                ? `기간 총액(1식 예산 × 끼니 수 × ${days}일)을 넘지 않는 선에서 어떤 끼니는 조금 더, 어떤 끼니는 조금 덜 쓸 수 있어요`
                : '매일 하루 예산(1식 예산 × 끼니 수)을 넘지 않게 짜요'}
            </div>
          </div>
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
        <Button type="primary" onClick={() => submit()} loading={genState === 'loading'} disabled={genState === 'loading'}>다음: 식단 생성 <ArrowRightOutlined /></Button>
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
