// src/pages/PlanCreate.tsx
// 식단 생성 1단계 — 조건 입력 (시안 04 · 04a 생성중 · 04b INFEASIBLE)
// 생성 호출까지 이 화면에서 처리하고, 성공하면 요청과 원시 응답을 들고 2단계(검토)로 넘어간다.
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Checkbox, InputNumber, Radio, Select, Spin, Tag } from 'antd';
import { ArrowRightOutlined } from '@ant-design/icons';
import Stepper from '../components/Stepper';
import AllergyGroupsField from '../components/AllergyGroupsField';
import { colors, radius } from '../theme';
import {
  generateMenu,
  isUnavailable,
  listProfiles,
  type AllergyGroup,
  type MenuGenerateRequest,
} from '../api/menu';

/**
 * GET /api/menu/profiles 실제 응답 (백엔드 schemas.UserProfileOut).
 * 출처: 2025 한국인 영양소 섭취기준 + 학교급식법 시행규칙 [별표3].
 */
interface Profile {
  profile_key: string;
  group_name: string;
  group_type?: string;
  sex?: string;
  age_band: string;
  daily_kcal: number;
  protein_g?: number | null;
  sodium_cdrr_mg?: number | null;
  sodium_ai_mg?: number | null;
  /** 학교급식법 [별표3] 1식 에너지(학생만) */
  legal_meal_kcal?: number | null;
  default_meals?: number;
  source?: string;
  note?: string;
}

/** 프로파일 표를 못 읽으면(503) 쓰는 폴백. 연결되면 응답이 우선한다. */
const FALLBACK_PROFILES: Profile[] = [
  {
    profile_key: 'elem_low_mix',
    group_name: '초등학생',
    age_band: '6-11세',
    daily_kcal: 1750,
    protein_g: 35,
    sodium_cdrr_mg: 1300,
    source: '폴백',
    note: '백엔드 프로파일 표를 읽지 못해 기본값을 표시 중입니다',
  },
];

/** 프로파일에서 나트륨 상한을 고른다. CDRR 우선, 없으면 AI. */
function sodiumOf(p?: Profile): number | undefined {
  return p?.sodium_cdrr_mg ?? p?.sodium_ai_mg ?? undefined;
}

/** 기저질환 — 시안 04 기준. CSP 요청 스키마에는 아직 자리가 없어 화면 상태로만 들고 있다. */
const CONDITIONS = ['고혈압', '당뇨', '신장질환'] as const;

const MEALS = ['아침', '점심', '저녁'] as const;

type Phase = 'form' | 'generating' | 'infeasible' | 'error';

const cardStyle = {
  borderRadius: radius.card,
  border: `1px solid ${colors.border}`,
};

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div style={{ fontSize: 12, color: colors.textSecondary, marginBottom: 6 }}>{label}</div>
      {children}
      {hint && !error && (
        <div style={{ fontSize: 11, color: colors.textTertiary, marginTop: 6 }}>{hint}</div>
      )}
      {error && <div style={{ fontSize: 11, color: colors.error, marginTop: 6 }}>{error}</div>}
    </div>
  );
}

export default function PlanCreate() {
  const navigate = useNavigate();

  /* ─── 대상 ─── */
  const [profiles, setProfiles] = useState<Profile[]>(FALLBACK_PROFILES);
  const [profileKey, setProfileKey] = useState<string>(FALLBACK_PROFILES[0].profile_key);
  const [conditions, setConditions] = useState<string[]>([]);
  const [headcount, setHeadcount] = useState<number>(320);

  /* ─── 생성 조건 ─── */
  const [days, setDays] = useState<7 | 31>(7);
  const [meals, setMeals] = useState<string[]>(['점심']);
  const [targetKcal, setTargetKcal] = useState<number>(1750);
  const [sodiumMax, setSodiumMax] = useState<number>(1300);
  const [budget, setBudget] = useState<number>(4500);
  /** 사용자가 열량·나트륨을 직접 고쳤으면 프로파일 자동 채움을 더 이상 덮어쓰지 않는다 */
  const touchedTargets = useRef(false);

  /* ─── 알레르기 ─── */
  const [allergyGroups, setAllergyGroups] = useState<AllergyGroup[]>([]);

  /* ─── 화면 상태 ─── */
  const [phase, setPhase] = useState<Phase>('form');
  const [errorMsg, setErrorMsg] = useState<string>('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const cancelled = useRef(false);

  const profile = useMemo(
    () => profiles.find((p) => p.profile_key === profileKey),
    [profiles, profileKey],
  );

  useEffect(() => {
    let alive = true;
    listProfiles()
      .then((res) => {
        const list = res as unknown as Profile[];
        if (!alive || !list?.length) return;
        setProfiles(list);
        if (!list.some((p: Profile) => p.profile_key === profileKey)) {
          setProfileKey(list[0].profile_key);
        }
      })
      .catch(() => {
        /* 503 등 — 폴백 목록 유지 */
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* 프로파일이 바뀌면 열량·나트륨을 기준값으로 채운다 (사용자가 손대기 전까지) */
  useEffect(() => {
    if (touchedTargets.current || !profile) return;
    if (profile.daily_kcal) setTargetKcal(profile.daily_kcal);
    const sodium = sodiumOf(profile);
    if (sodium) setSodiumMax(sodium);
  }, [profile]);

  function validate(): Record<string, string> {
    const e: Record<string, string> = {};
    if (!profileKey) e.profileKey = '대상 프로파일을 선택하세요.';
    if (!headcount || headcount < 1) e.headcount = '인원수는 1명 이상이어야 합니다.';
    if (meals.length === 0) e.meals = '끼니를 하나 이상 선택하세요.';
    if (!targetKcal || targetKcal < 500 || targetKcal > 5000)
      e.targetKcal = '1일 열량 목표는 500~5,000 kcal 범위로 입력하세요.';
    if (!sodiumMax || sodiumMax < 100 || sodiumMax > 5000)
      e.sodiumMax = '나트륨 상한은 100~5,000 mg 범위로 입력하세요.';
    if (budget != null && budget < 0) e.budget = '예산은 0원 이상이어야 합니다.';

    const emptyGroup = allergyGroups.some((g) => g.allergens.length === 0);
    if (emptyGroup) e.allergy = '알레르기 항목이 비어 있는 그룹이 있습니다.';
    const badCount = allergyGroups.some((g) => !g.count || g.count < 1);
    if (badCount) e.allergy = '알레르기 그룹의 인원수는 1명 이상이어야 합니다.';

    return e;
  }

  function buildRequest(): MenuGenerateRequest {
    return {
      profile_key: profileKey,
      days,
      meals,
      target_kcal_per_day: targetKcal,
      sodium_max_mg_per_day: sodiumMax,
      budget_limit_per_person: budget,
      with_alternatives: allergyGroups.length > 0,
      allergy_groups: allergyGroups,
    };
  }

  async function handleSubmit() {
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length > 0) return;

    const request = buildRequest();
    cancelled.current = false;
    setPhase('generating');

    try {
      const raw = await generateMenu(request);
      if (cancelled.current) return;

      const status = String(raw?.status ?? '').toUpperCase();
      if (status.includes('INFEASIBLE')) {
        setPhase('infeasible');
        return;
      }

      // 2단계(검토)로. 원시 응답을 같이 넘겨 결과 화면이 다시 호출하지 않아도 되게 한다.
      navigate('/plans/result', {
        state: {
          request,
          raw,
          // CSP 요청 스키마에 자리가 없는 값들. 결과 화면 요약·달성률에서 쓴다.
          meta: { headcount, conditions, proteinTargetG: profile?.protein_g ?? null },
        },
      });
    } catch (err) {
      if (cancelled.current) return;
      const res = (err as { response?: { status?: number; data?: unknown } })?.response;

      // 응답 자체가 없으면 연결 실패, 503 이면 인프라 준비 중 — 둘 다 조건 문제가 아니다.
      if (!res || isUnavailable(err)) {
        setErrorMsg(
          !res
            ? '식단 생성 서버에 연결할 수 없어요. 백엔드가 실행 중인지 확인해 주세요.'
            : '식단 생성 기능이 아직 준비 중입니다. 잠시 후 다시 시도해 주세요.',
        );
        setPhase('error');
        return;
      }

      // 백엔드가 INFEASIBLE 을 본문에 담아 4xx 로 주는 경우
      if (JSON.stringify(res.data ?? '').toUpperCase().includes('INFEASIBLE')) {
        setPhase('infeasible');
        return;
      }

      setErrorMsg('식단 생성에 실패했어요. 잠시 후 다시 시도해 주세요.');
      setPhase('error');
    }
  }

  const disabled = phase === 'generating';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, position: 'relative' }}>
      {/* 타이틀 + 스텝퍼 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700, color: colors.text }}>식단 생성</div>
          <div style={{ marginTop: 4, fontSize: 13, color: colors.textSecondary }}>
            {phase === 'generating'
              ? '1단계. 조건을 만족하는 식단을 찾는 중'
              : phase === 'infeasible'
                ? '1단계. 조건을 만족하는 식단이 없어요'
                : '1단계. 대상과 조건을 입력하세요'}
          </div>
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <Stepper current={1} />
        </div>
      </div>

      {phase === 'error' && (
        <Alert
          type="error"
          showIcon
          message="식단을 생성하지 못했어요"
          description={errorMsg}
          action={
            <Button size="small" onClick={() => setPhase('form')}>
              닫기
            </Button>
          }
        />
      )}

      {phase === 'infeasible' && (
        <Alert
          type="warning"
          showIcon
          message="조건을 만족하는 식단을 찾지 못했습니다"
          description="입력한 조건이 서로 충돌해 해를 찾지 못했습니다. 예산, 나트륨 상한, 알레르기 제외 범위, 끼니 구성 같은 조건을 조정하면 다시 생성할 수 있습니다."
          action={
            <Button size="small" onClick={() => setPhase('form')}>
              조건 수정하기
            </Button>
          }
        />
      )}

      {/* 카드 1 — 대상 */}
      <Card style={cardStyle} styles={{ body: { padding: 20 } }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: colors.text, marginBottom: 16 }}>
          대상
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
          <Field label="대상 프로파일" error={errors.profileKey}>
            <Select
              disabled={disabled}
              value={profileKey}
              onChange={setProfileKey}
              style={{ width: '100%' }}
              options={profiles.map((p) => ({
                value: p.profile_key,
                label: p.group_name ?? p.profile_key,
              }))}
            />
          </Field>

          <Field
            label="연령대 (자동)"
            hint={[profile?.source, profile?.note].filter(Boolean).join(' · ') || undefined}
          >
            <div
              style={{
                height: 36,
                display: 'flex',
                alignItems: 'center',
                padding: '0 12px',
                borderRadius: radius.input,
                background: colors.bgLayout,
                border: `1px solid ${colors.borderSubtle}`,
                fontSize: 13,
                color: colors.textSecondary,
              }}
            >
              {profile?.age_band ?? '—'}
            </div>
          </Field>

          <Field label="기저질환 (다중 선택)">
            <Checkbox.Group
              disabled={disabled}
              value={conditions}
              onChange={(v) => setConditions(v as string[])}
              options={CONDITIONS.map((c) => ({ label: c, value: c }))}
            />
          </Field>

          <Field label="인원수" error={errors.headcount}>
            <InputNumber
              disabled={disabled}
              min={1}
              max={99999}
              value={headcount}
              onChange={(v) => setHeadcount(v ?? 0)}
              addonAfter="명"
              style={{ width: '100%' }}
            />
          </Field>
        </div>
      </Card>

      {/* 카드 2 — 생성 조건 */}
      <Card style={cardStyle} styles={{ body: { padding: 20 } }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: colors.text, marginBottom: 16 }}>
          생성 조건
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
          <Field label="기간">
            <Radio.Group
              disabled={disabled}
              value={days}
              onChange={(e) => setDays(e.target.value)}
              optionType="button"
              options={[
                { label: '7일', value: 7 },
                { label: '31일', value: 31 },
              ]}
            />
          </Field>

          <Field label="끼니" error={errors.meals}>
            <Checkbox.Group
              disabled={disabled}
              value={meals}
              onChange={(v) => setMeals(v as string[])}
              options={MEALS.map((m) => ({ label: m, value: m }))}
            />
          </Field>

          <Field
            label="1일 열량 목표"
            hint={
              profile?.legal_meal_kcal
                ? `프로파일 기준 자동 채움 · 학교급식법 1식 ${profile.legal_meal_kcal.toLocaleString()} kcal`
                : '프로파일 기준 자동 채움'
            }
            error={errors.targetKcal}
          >
            <InputNumber
              disabled={disabled}
              min={500}
              max={5000}
              step={50}
              value={targetKcal}
              onChange={(v) => {
                touchedTargets.current = true;
                setTargetKcal(v ?? 0);
              }}
              addonAfter="kcal"
              style={{ width: '100%' }}
            />
          </Field>

          <Field label="나트륨 상한" hint="프로파일 기준 자동 채움" error={errors.sodiumMax}>
            <InputNumber
              disabled={disabled}
              min={100}
              max={5000}
              step={50}
              value={sodiumMax}
              onChange={(v) => {
                touchedTargets.current = true;
                setSodiumMax(v ?? 0);
              }}
              addonAfter="mg"
              style={{ width: '100%' }}
            />
          </Field>

          <Field label="1인 1식 예산" error={errors.budget}>
            <InputNumber
              disabled={disabled}
              min={0}
              max={100000}
              step={100}
              value={budget}
              onChange={(v) => setBudget(v ?? 0)}
              addonAfter="원"
              style={{ width: '100%' }}
              formatter={(v) => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
              parser={(v) => Number((v ?? '').replace(/,/g, ''))}
            />
          </Field>
        </div>
      </Card>

      {/* 카드 3 — 알레르기 그룹 */}
      <Card style={cardStyle} styles={{ body: { padding: 20 } }}>
        <AllergyGroupsField
          value={allergyGroups}
          onChange={setAllergyGroups}
          headcount={headcount}
          error={errors.allergy}
          disabled={disabled}
        />
      </Card>

      {/* 하단 액션 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ fontSize: 12, color: colors.textTertiary }}>
          {profile?.group_name ?? profileKey} · {headcount.toLocaleString()}명 · {days}일 ·{' '}
          {meals.join('·') || '끼니 미선택'} · 알레르기 {allergyGroups.length}그룹
          {budget ? ` · 예산 ${budget.toLocaleString()}원/식` : ''}
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Button disabled={disabled} onClick={() => navigate(-1)}>
            취소
          </Button>
          <Button
            type="primary"
            loading={disabled}
            onClick={handleSubmit}
            icon={<ArrowRightOutlined />}
            iconPosition="end"
          >
            다음: 식단 생성
          </Button>
        </div>
      </div>

      {/* 04a — 생성 중 오버레이 */}
      {phase === 'generating' && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'rgba(251, 253, 252, 0.72)',
            backdropFilter: 'blur(3px)',
          }}
        >
          <div
            style={{
              width: 360,
              padding: '28px 24px',
              borderRadius: radius.card,
              background: colors.bgContainer,
              border: `1px solid ${colors.border}`,
              textAlign: 'center',
            }}
          >
            <Spin size="large" />
            <div style={{ marginTop: 18, fontSize: 15, fontWeight: 600, color: colors.text }}>
              식단을 생성하고 있습니다
            </div>
            <div style={{ marginTop: 6, fontSize: 12, color: colors.textSecondary }}>
              보통 30~50초 · 제약조건 최적화 중
            </div>
            <div style={{ marginTop: 14 }}>
              <Tag color="default">창을 닫아도 생성은 계속됩니다</Tag>
            </div>
            <Button
              style={{ marginTop: 16 }}
              onClick={() => {
                cancelled.current = true;
                setPhase('form');
              }}
            >
              취소
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}