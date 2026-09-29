// src/pages/PlanCreate.tsx
// 식단 생성 위저드 — 1.조건입력 → 2.검토 → 3.확정 (시안 화면 4·5·6)
// 데이터: POST /api/menu/generate. 해 없음(INFEASIBLE)은 조건 충돌 화면, 서버 오류는
// 개발 모드에서만 목업 폴백(화면·상호작용 확인용), 프로덕션에서는 오류 메시지.
import { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { App, Card, Skeleton } from 'antd';
import Step1Conditions, { type Step1Form } from './plan/Step1Conditions';
import Step2Review from './plan/Step2Review';
import Step3Confirm from './plan/Step3Confirm';
import { planToStep1Form } from './plan/cloneForm';
import {
  generateMenu, toMealPlan, mockPlan, isInfeasibleResponse, isUnavailable, savePlan, getSavedPlan,
  type MenuGenerateRequest, type MenuGenerateRaw, type MealPlan,
} from '../api/menu';

type GenState = 'idle' | 'loading' | 'infeasible';

// 상단 '새 식단'을 다시 눌러 /plans/new 로 재진입하면(같은 URL이어도 location.key 가 바뀜)
// 위저드를 1단계부터 새로 시작한다. (이미 검토·확정 단계에 있으면 아무 반응 없어 보이던 버그 수정)
// effect 로 state 를 되돌리는 대신 key 로 새로 마운트한다.
// 식단 목록의 '복제해서 만들기'는 navigate('/plans/new', { state: { cloneId } }) 로 들어온다.
// 저장본을 불러와 1단계 입력값을 채운 채로 위저드를 시작한다.
export default function PlanCreate() {
  const location = useLocation();
  const cloneId = (location.state as { cloneId?: number } | null)?.cloneId;
  return cloneId != null
    ? <ClonedWizard key={location.key} cloneId={cloneId} />
    : <PlanWizard key={location.key} />;
}

function ClonedWizard({ cloneId }: { cloneId: number }) {
  const { message } = App.useApp();
  const [init, setInit] = useState<{ form?: Step1Form; name?: string } | null>(null);

  useEffect(() => {
    let alive = true;
    getSavedPlan(cloneId)
      .then((saved) => {
        if (!alive) return;
        setInit({ form: planToStep1Form(saved.plan), name: saved.name });
        message.info(`'${saved.name}' 조건을 불러왔어요. 기저질환·알레르기 그룹은 저장되지 않아 다시 입력해 주세요.`);
      })
      .catch((e) => {
        console.error('[식단복제] 저장본 조회 실패:', e);
        if (!alive) return;
        message.error('복제할 식단을 불러오지 못해 기본 조건으로 시작합니다.');
        setInit({});
      });
    return () => { alive = false; };
  }, [cloneId, message]);

  if (!init) return <Card><Skeleton active paragraph={{ rows: 6 }} /></Card>;
  return <PlanWizard initialForm={init.form} />;
}

function PlanWizard({ initialForm }: { initialForm?: Step1Form }) {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [step, setStep] = useState(1);
  const [genState, setGenState] = useState<GenState>('idle');
  const [plan, setPlan] = useState<MealPlan | null>(null);
  const [form, setForm] = useState<Step1Form | undefined>(initialForm); // 조건 수정·복제 시 입력값 복원용

  const onGenerate = async (req: MenuGenerateRequest) => {
    setGenState('loading');
    let raw: MenuGenerateRaw;
    try {
      raw = await generateMenu(req);
    } catch (e) {
      // 진짜 서버 오류(503/네트워크)만 여기로 온다. 목업은 개발 서버에서만 쓰고,
      // 프로덕션 빌드에서는 가짜 식단을 보여주지 않고 오류를 알린다.
      if (import.meta.env.DEV) {
        console.warn('[식단생성] 실서버 호출 실패 → 개발 모드 목업(mock)으로 대체합니다:', e);
        message.info('데모용 목업 식단입니다 (실서버 미연동 · 개발 모드). 콘솔 로그를 확인하세요.');
        showPlan(mockPlan(req));
      } else {
        console.error('[식단생성] 실서버 호출 실패:', e);
        message.error(isUnavailable(e)
          ? '식단 생성 서버를 사용할 수 없습니다(503). 잠시 후 다시 시도해 주세요.'
          : '식단 생성 요청에 실패했습니다. 네트워크 상태를 확인해 주세요.');
        setGenState('idle');
      }
      return;
    }
    if (isInfeasibleResponse(raw)) {
      // 서버가 실제로 조건을 풀었지만 해가 없다(INFEASIBLE 등) — 목업으로 감추지 않고
      // 시안 4-b의 "조건 충돌" 화면을 보여준다. 콘솔에 원인(status)을 남긴다.
      console.warn('[식단생성] 실서버가 해를 찾지 못했습니다:', raw.status);
      setGenState('infeasible');
      return;
    }
    try {
      showPlan(toMealPlan(raw, req));
    } catch (e) {
      // 응답 매핑 실패는 프론트 버그이므로 목업으로 감추지 않는다.
      console.error('[식단생성] 응답 변환 실패:', e);
      message.error('식단 결과를 화면에 표시하지 못했습니다. 콘솔 로그를 확인하세요.');
      setGenState('idle');
    }
  };
  const showPlan = (result: MealPlan) => {
    setPlan(result);
    setGenState('idle');
    setStep(2);
  };
  // 검토에서 교체·삭제한 결과까지 포함한 현재 뷰모델을 그대로 저장한다. 실패하면 머물러 다시 시도할 수 있게 한다.
  const saveDraft = async (name: string) => {
    if (!plan) return;
    try {
      await savePlan(name, plan);
    } catch (e) {
      console.error('[식단저장] 저장 실패:', e);
      message.error('식단을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.');
      throw e;
    }
    message.success(`'${name}' 식단을 저장했어요`);
    navigate('/plans');
  };
  // 확정 — status '확정'으로 저장한다(식단 목록 '확정' 배지·필터, 홈 확정완료의 근거).
  // CSV 내려받기는 Step3Confirm 이 저장 성공 후에 한다. 시안 02e 대로 확정 직후 홈으로 이동.
  const confirmPlan = async (name: string) => {
    if (!plan) return;
    let id: number;
    try {
      ({ id } = await savePlan(name, { ...plan, status: '확정' }));
    } catch (e) {
      console.error('[식단확정] 저장 실패:', e);
      message.error('식단을 확정하지 못했습니다. 잠시 후 다시 시도해 주세요.');
      throw e;
    }
    // CSV 연속 다운로드가 시작될 시간을 두고 홈으로(확정 완료 토스트는 홈이 띄운다)
    setTimeout(() => navigate('/', { state: { confirmedId: id } }), 1200);
  };

  return (
    <div>
      {step === 1 && (
        <Step1Conditions genState={genState} onGenerate={onGenerate} onCancel={() => navigate('/')} initial={form} onFormChange={setForm} />
      )}
      {step === 2 && plan && (
        <Step2Review
          plan={plan}
          setPlan={setPlan}
          onPrev={() => setStep(1)}
          onNext={() => setStep(3)}
          onEditConditions={() => setStep(1)}
        />
      )}
      {step === 3 && plan && (
        <Step3Confirm
          plan={plan}
          onPrev={() => setStep(2)}
          onSaveDraft={saveDraft}
          onConfirm={confirmPlan}
        />
      )}
    </div>
  );
}