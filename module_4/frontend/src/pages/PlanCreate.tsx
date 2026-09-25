// src/pages/PlanCreate.tsx
// 식단 생성 위저드 — 1.조건입력 → 2.검토 → 3.확정 (시안 화면 4·5·6)
// 데이터: POST /api/menu/generate. 해 없음(INFEASIBLE)은 조건 충돌 화면, 서버 오류는
// 개발 모드에서만 목업 폴백(화면·상호작용 확인용), 프로덕션에서는 오류 메시지.
import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { App } from 'antd';
import Step1Conditions, { type Step1Form } from './plan/Step1Conditions';
import Step2Review from './plan/Step2Review';
import Step3Confirm from './plan/Step3Confirm';
import {
  generateMenu, toMealPlan, mockPlan, isInfeasibleResponse, isUnavailable,
  type MenuGenerateRequest, type MenuGenerateRaw, type MealPlan,
} from '../api/menu';

type GenState = 'idle' | 'loading' | 'infeasible';

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
          // 식단 저장 API가 아직 없다 — 저장된 척하고 목록으로 이동하면 방금 만든 식단을 잃으므로 사실대로 안내하고 머문다.
          // TODO: 식단 저장 API 연동 후 실제 저장 → /plans 이동
          onSaveDraft={() => message.info('식단 저장 기능은 준비 중이에요. 지금은 CSV로 내려받아 보관해 주세요.')}
        />
      )}
    </div>
  );
}
