// src/pages/plan/GeneratingOverlay.tsx
// 식단 생성 중 화면 — 로고(그릇 + 밥풀 3점이 반원을 그리며 지나가는) 애니메이션, 조건 요약, 예상·경과 시간, 진행 막대, 안내 문구 순환.
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
const CRAWL_SECONDS = 2.6; // 밥풀 3점이 그릇 위를 한 번 지나가는 주기
const MAX_BEFORE_RESPONSE = 0.95;

// 밥풀 3점이 지나가는 길 — 그릇(중심 24,26 · 반지름 19) 폭 안쪽에 들어가는 반타원. 좌표는 로고 viewBox(48×48) 단위.
// 각도는 화면 기준(시계 방향, 270°가 꼭대기). 시작·끝(155°·385°)은 그릇 윗선보다 아래라 그릇에 가려져, 점이 그릇에서 나와 그릇으로 들어가는 것처럼 보인다.
const ARC = { cx: 24.5, cy: 26, rx: 12, ry: 14.5, fromDeg: 155, toDeg: 385, steps: 23, moveRatio: 0.65 };
// 점의 제자리(components/Logo.tsx 의 cx·cy 와 같아야 한다)와 출발 지연(주기 대비). 큰 점이 앞장선다.
const DOTS = [
  { cls: 'logo-steam-3', name: 'genCrawl3', x: 35, y: 14, delay: 0 },
  { cls: 'logo-steam-2', name: 'genCrawl2', x: 24, y: 11.5, delay: 0.15 },
  { cls: 'logo-steam-1', name: 'genCrawl1', x: 14, y: 15, delay: 0.3 },
];
// 제자리 (x, y) 에 있는 점을 ARC 위로 옮기는 keyframes. 주기의 moveRatio 동안 움직이고 나머지는 끝(그릇 안)에서 기다린다.
function crawlKeyframes(name: string, x: number, y: number): string {
  const at = (deg: number) => {
    const rad = (deg * Math.PI) / 180;
    const dx = ARC.cx + ARC.rx * Math.cos(rad) - x;
    const dy = ARC.cy + ARC.ry * Math.sin(rad) - y;
    return `transform: translate(${dx.toFixed(2)}px, ${dy.toFixed(2)}px);`;
  };
  const frames: string[] = [];
  for (let k = 0; k <= ARC.steps; k += 1) {
    const t = k / ARC.steps;
    frames.push(`${(t * ARC.moveRatio * 100).toFixed(1)}% { ${at(ARC.fromDeg + (ARC.toDeg - ARC.fromDeg) * t)} }`);
  }
  frames.push(`100% { ${at(ARC.toDeg)} }`);
  return `@keyframes ${name} { ${frames.join(' ')} }`;
}

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
        /* 밥풀 3점: 그릇 안(왼쪽)에서 큰 점(3)이 먼저 올라오고 2·1이 애벌레처럼 뒤따라, 그릇 위로 반원을 그리며
           그릇 안(오른쪽)으로 들어간다. 경로가 그릇 폭 안쪽이라 점이 그릇 옆으로 삐져나오지 않는다. 한 주기 ${CRAWL_SECONDS}초 */
        .gen-logo .logo-steam { animation: ${CRAWL_SECONDS}s linear infinite both; }
        ${DOTS.map((d) => `.gen-logo .${d.cls} { animation-name: ${d.name}; animation-delay: ${(CRAWL_SECONDS * d.delay).toFixed(2)}s; }`).join('\n        ')}
        ${DOTS.map((d) => crawlKeyframes(d.name, d.x, d.y)).join('\n        ')}
        /* 그릇: 같은 박자로 약 1.5px(viewBox 0.9) 들썩임 */
        .gen-logo .logo-bowl { animation: genBowl ${CRAWL_SECONDS}s ease-in-out infinite; }
        @keyframes genBowl {
          0%, 100% { transform: translateY(0); }
          20% { transform: translateY(0.9px); }
          45% { transform: translateY(-0.9px); }
          70% { transform: translateY(0); }
        }
        .gen-bar { transition: width 0.3s linear; }
        .gen-tip { animation: genTip ${TIP_SECONDS}s ease-in-out both; }
        @keyframes genTip { 0% { opacity: 0; } 12% { opacity: 1; } 88% { opacity: 1; } 100% { opacity: 0; } }
        /* '동작 줄이기'(Windows 애니메이션 효과 끔) 설정에서도 로딩 아이콘은 움직인다 — 진행 중임을 알리는 표시라 멈추지 않는다.
           안내 문구 전환과 진행 막대의 부드러운 움직임만 끈다. */
        @media (prefers-reduced-motion: reduce) {
          .gen-tip { animation: none; }
          .gen-bar { transition: none; }
        }
      `}</style>
    </div>
  );
}