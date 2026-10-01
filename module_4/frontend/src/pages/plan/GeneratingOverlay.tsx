// src/pages/plan/GeneratingOverlay.tsx
// 식단 생성 중 화면 — 로고(그릇 + 김 3점) 애니메이션, 조건 요약, 예상·경과 시간, 진행 막대, 안내 문구 순환.
// 진행 막대는 실제 진행률이 아니라 경과 ÷ 예상 시간이다(서버가 중간 진행을 알려주지 않음) — 응답 전에는 95%에서 멈춘다.
// Step1 이 genState==='loading' 일 때만 그리므로 [다시 시도]로 다시 생성하면 새로 마운트되어 시간이 0부터 다시 잰다.
import { useEffect, useState } from 'react';
import { LogoMark } from '../../components/Logo';
import { estimateSeconds, type GeneratingInfo } from './generatingInfo';

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7', line: '#EEF2F0', green: '#12A150',
};

const MEAL_LABEL: Record<string, string> = { 아침: '조식', 점심: '중식', 저녁: '석식' };

// 특정 시점에 특정 단계를 하는 것처럼 보이지 않게 — 풀이 전체에서 함께 지키는 조건들을 번갈아 보여줄 뿐이다.
const TIPS = [
  '영양·예산·나트륨 조건을 함께 맞추고 있어요',
  '3일 안에 같은 메뉴가 겹치지 않게 고르고 있어요',
  '주식·국·반찬 구성을 맞추고 있어요',
];
const ALLERGY_TIP = '알레르기 그룹 대체식을 준비하고 있어요';
const TIP_SECONDS = 4;
const MAX_BEFORE_RESPONSE = 0.95;

export default function GeneratingOverlay({ info }: { info: GeneratingInfo }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = performance.now();
    const timer = setInterval(() => setElapsed((performance.now() - started) / 1000), 250);
    return () => clearInterval(timer); // 응답·실패로 화면이 바뀌면(언마운트) 바로 정리
  }, []);

  const est = estimateSeconds(info.days, info.meals.length);
  const over = elapsed > est;
  const ratio = Math.min(elapsed / est, MAX_BEFORE_RESPONSE);
  const tips = info.hasAllergy ? [...TIPS, ALLERGY_TIP] : TIPS;
  const tipIndex = Math.floor(elapsed / TIP_SECONDS) % tips.length;
  const sec = Math.floor(elapsed);
  const mealsText = info.meals.map((m) => MEAL_LABEL[m] ?? m).join('·');

  return (
    <div role="status" aria-live="polite"
      style={{ position: 'absolute', inset: 0, background: 'rgba(247,250,248,0.72)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10 }}>
      <div style={{ width: 400, background: '#fff', border: `1px solid ${C.border}`, borderRadius: 14, padding: '30px 28px 26px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, boxShadow: '0 16px 40px rgba(22,33,28,0.12)' }}>
        <div className="gen-logo" style={{ padding: '10px 0 6px' }}>
          <LogoMark size={80} animated />
        </div>
        <div style={{ fontSize: 17, fontWeight: 600, color: C.text }}>식단을 만들고 있어요</div>
        <div style={{ fontSize: 13, color: C.sub }}>평일 {info.days}일 · {mealsText} · {info.headcount.toLocaleString()}명</div>
        <div data-testid="gen-time" style={{ fontSize: 12, color: over ? C.text : C.muted, fontVariantNumeric: 'tabular-nums' }}>
          {over ? `조금 더 걸리고 있어요 · ${sec}초 경과` : `예상 약 ${est}초 · ${sec}초 경과`}
        </div>
        <div style={{ width: '100%', height: 4, borderRadius: 2, background: C.line, overflow: 'hidden', marginTop: 4 }}>
          <div data-testid="gen-bar" className="gen-bar" style={{ width: `${(ratio * 100).toFixed(1)}%`, height: '100%', background: C.green, borderRadius: 2 }} />
        </div>
        <div style={{ minHeight: 20, marginTop: 6 }}>
          <div key={tipIndex} className="gen-tip" data-testid="gen-tip" style={{ fontSize: 13, color: C.sub, textAlign: 'center' }}>{tips[tipIndex]}</div>
        </div>
      </div>
      <style>{`
        /* 김: 왼쪽부터 0.15초 간격으로 위로 떠오르며(약 7px = viewBox 4.2 × 80/48) 옅어졌다가 제자리로. 한 주기 1.4초 */
        .gen-logo .logo-steam { transform-box: fill-box; transform-origin: center; animation: genSteam 1.4s ease-in-out infinite; }
        .gen-logo .logo-steam-2 { animation-delay: 0.15s; }
        .gen-logo .logo-steam-3 { animation-delay: 0.3s; }
        @keyframes genSteam {
          0%, 100% { transform: translateY(0); opacity: 1; }
          45% { transform: translateY(-4.2px); opacity: 0.3; }
          70% { transform: translateY(0); opacity: 1; }
        }
        /* 그릇: 같은 1.4초 박자로 약 1.5px(viewBox 0.9) 들썩임 */
        .gen-logo .logo-bowl { animation: genBowl 1.4s ease-in-out infinite; }
        @keyframes genBowl {
          0%, 100% { transform: translateY(0); }
          20% { transform: translateY(0.9px); }
          45% { transform: translateY(-0.9px); }
          70% { transform: translateY(0); }
        }
        .gen-bar { transition: width 0.3s linear; }
        .gen-tip { animation: genTip ${TIP_SECONDS}s ease-in-out both; }
        @keyframes genTip { 0% { opacity: 0; } 12% { opacity: 1; } 88% { opacity: 1; } 100% { opacity: 0; } }
        @media (prefers-reduced-motion: reduce) {
          .gen-logo .logo-steam, .gen-logo .logo-bowl, .gen-tip { animation: none; }
          .gen-bar { transition: none; }
        }
      `}</style>
    </div>
  );
}
