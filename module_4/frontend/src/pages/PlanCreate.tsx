// src/pages/PlanCreate.tsx
// 식단 생성 위저드 — 1.조건입력 → 2.검토 → 3.확정 (시안 화면 4·5·6)
// 데이터: POST /api/menu/generate. 503(CSP 미통합)이면 목업 폴백(화면·상호작용 확인용).
import { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { message } from 'antd';
import Step1Conditions, { type Step1Form } from './plan/Step1Conditions';
import Step2Review from './plan/Step2Review';
import Step3Confirm from './plan/Step3Confirm';
import { generateMenu, toMealPlan, mockPlan, type MenuGenerateRequest, type MealPlan } from '../api/menu';

type GenState = 'idle' | 'loading' | 'infeasible';

export default function PlanCreate() {
  const navigate = useNavigate();
  const location = useLocation();
  const [step, setStep] = useState(1);
  const [genState, setGenState] = useState<GenState>('idle');
  const [plan, setPlan] = useState<MealPlan | null>(null);
  const [form, setForm] = useState<Step1Form | undefined>(undefined); // 조건 수정 시 입력값 복원용

  // 상단 '새 식단'을 다시 눌러 /plans/new 로 재진입하면(같은 URL이어도 location.key 가 바뀜)
  // 위저드를 1단계부터 새로 시작한다. (이미 검토·확정 단계에 있으면 아무 반응 없어 보이던 버그 수정)
  useEffect(() => {
    setStep(1);
    setGenState('idle');
    setPlan(null);
    setForm(undefined);
  }, [location.key]);

  const onGenerate = async (req: MenuGenerateRequest) => {
    // 데모: 예산이 지나치게 낮으면 INFEASIBLE 화면(시안 4-b)
    if ((req.budget_limit_per_person ?? 0) < 3500) { setGenState('infeasible'); return; }
    setGenState('loading');
    let result: MealPlan;
    try {
      const raw = await generateMenu(req);
      if (raw?.status && raw.status !== 'OPTIMAL' && raw.status !== 'FEASIBLE') {
        // 서버가 실제로 조건을 풀었지만 해가 없다는 뜻(INFEASIBLE 등) — 목업으로 감추지 않고
        // 시안 4-b의 "조건 충돌" 화면을 그대로 보여준다. 콘솔에 원인 배지(status)를 남긴다.
        console.warn('[식단생성] 실서버가 INFEASIBLE을 반환했습니다:', raw.status);
        setGenState('infeasible');
        return;
      }
      result = toMealPlan(raw, req);            // 실제 응답 매핑(미구현/미기동 시 예외)
    } catch (e) {
      // 실서버 실패(503/네트워크) 또는 응답 매핑 실패 → 목업 폴백. 콘솔에서 원인을 확인할 수 있게 남긴다.
      console.warn('[식단생성] 실서버 응답 사용 실패 → 목업(mock)으로 대체합니다:', e);
      await new Promise((r) => setTimeout(r, 900)); // 생성중 화면을 잠깐 보여줌
      result = mockPlan(req);                    // 503/미구현 → 목업 폴백
    }
    if (result.source === 'mock') {
      message.info('데모용 목업 식단입니다 (실서버 미연동). 콘솔 로그를 확인하세요.');
    }
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
          onSaveDraft={() => { message.success('초안으로 저장했어요'); navigate('/plans'); }}
        />
      )}
    </div>
  );
}
