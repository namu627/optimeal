// src/pages/PlanCreate.tsx
// 식단 생성 위저드 — 1.조건입력 → 2.검토 → 3.확정 (시안 화면 4·5·6)
// 데이터: POST /api/menu/generate. 해 없음(INFEASIBLE)은 조건 충돌 화면, 요청 실패는 원인별 실패 화면
// (백엔드 연결 실패 / DB 연결 실패 / 서버 오류). 실패를 목업으로 대체하지 않는다 —
// 목업은 VITE_USE_MOCK=true 일 때만(USE_MOCK) 쓰고, 그때는 서버를 부르지 않는다.
import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { App } from 'antd';
import Step1Conditions, { type Step1Form } from './plan/Step1Conditions';
import Step2Review from './plan/Step2Review';
import Step3Confirm from './plan/Step3Confirm';
import {
  generateMenu, toMealPlan, mockPlan, isInfeasibleResponse, isTimeoutResponse, isSolverBusy, savePlan,
  classifyGenerateError, USE_MOCK,
  type MenuGenerateRequest, type MenuGenerateRaw, type MealPlan, type GenerateError,
} from '../api/menu';

type GenState = 'idle' | 'loading' | 'infeasible' | 'timeout' | 'error';

// 상단 '새 식단'을 다시 눌러 /plans/new 로 재진입하면(같은 URL이어도 location.key 가 바뀜)
// 위저드를 1단계부터 새로 시작한다. (이미 검토·확정 단계에 있으면 아무 반응 없어 보이던 버그 수정)
// effect 로 state 를 되돌리는 대신 key 로 새로 마운트한다.
export default function PlanCreate() {
  const location = useLocation();
  return <PlanWizard key={location.key} />;
}

function PlanWizard() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [step, setStep] = useState(1);
  const [genState, setGenState] = useState<GenState>('idle');
  const [plan, setPlan] = useState<MealPlan | null>(null);
  const [form, setForm] = useState<Step1Form | undefined>(undefined); // 조건 수정 시 입력값 복원용
  const [genError, setGenError] = useState<GenerateError | null>(null);

  const onGenerate = async (req: MenuGenerateRequest) => {
    if (genState === 'loading') return; // 중복 요청 방지 — 겹치면 서버에서 두 풀이가 CPU 를 나눠 둘 다 시간 초과
    if (USE_MOCK) { showPlan(mockPlan(req)); return; } // 명시적 목업 스위치 — 상단 배너·내보내기 차단은 AppLayout·Step3
    setGenState('loading');
    setGenError(null);
    let raw: MenuGenerateRaw;
    try {
      raw = await generateMenu(req);
    } catch (e) {
      if (isSolverBusy(e)) {
        message.warning('다른 식단을 생성하는 중이에요. 끝난 뒤 다시 시도해 주세요.');
        setGenState('idle');
        return;
      }
      // 진짜 요청 실패(네트워크·503·500)만 여기로 온다. 목업으로 감추지 않고 원인별 실패 화면을 보여준다.
      const ge = classifyGenerateError(e);
      console.error(`[식단생성] 실서버 호출 실패(${ge.kind}):`, e);
      setGenError(ge);
      setGenState('error');
      return;
    }
    if (isTimeoutResponse(raw)) {
      // 시간 안에 해를 못 찾음(UNKNOWN) — 조건 충돌이 아니므로 다른 안내(다시 시도·기간 줄이기·하루 단위)로.
      console.warn('[식단생성] 시간 안에 해를 찾지 못했습니다:', raw.status, raw.stop_reason);
      setGenState('timeout');
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

  return (
    <div>
      {step === 1 && (
        <Step1Conditions genState={genState} genError={genError} onGenerate={onGenerate} onCancel={() => navigate('/')} initial={form} onFormChange={setForm} />
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
        />
      )}
    </div>
  );
}
