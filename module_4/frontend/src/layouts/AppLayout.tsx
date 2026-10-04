// src/layouts/AppLayout.tsx
// 공통 레이아웃 — 시안 Shell + 화면 02a(프로필 드롭다운)·02d(사이드바 접힘), 컨벤션 §3·§5.
//  · 사이드바: 주 메뉴(홈/식단 생성/식단 목록) → "도구" 라벨+구분선 → 도구 메뉴 → 설정 → 하단(프로필·백엔드 상태·접기)
//  · 활성 메뉴: 내용 폭만 감싸는 알약(높이 34, 라운드 17, 틴트 배경)
//  · 상단 바: 제목·보조 설명 / + 새 식단 / 프로필 아바타(클릭 시 드롭다운). 알림·전역 검색은 범위 밖(컨벤션 §3)
//  · 백엔드 상태(정상 / 백엔드 연결 안 됨 / DB 연결 안 됨): 상단 바 아래 코럴 배너 + 사이드바 상태 점,
//    백엔드에 닿지 않으면 새 식단 비활성(컨벤션 §5). 목업 스위치(VITE_USE_MOCK)가 켜지면 머스터드 배너.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { colors, layout } from '../theme';
import { LogoMark } from '../components/Logo';
import { useHealth } from '../api/useHealth';
import { USE_MOCK, MOCK_BANNER } from '../api/menu';

// 로그인·계정 API가 아직 없어 사용자·소속 정보가 없다(컨벤션 §6 — 계정은 목업). 가짜 이름 대신 중립 표기.
// TODO: 로그인 연동 시 실제 사용자명·소속 업장으로 교체
const USER_NAME = '영양사';
const USER_ORG = '소속 업장 미설정';

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', faint: '#8A9691',
  line: '#EEF2F0', border: '#E5EAE7', hover: '#F2F6F3',
  tint: '#E4F7EB', greenText: '#0B6B36', green: '#12A150', greenHover: '#0E8A44',
  red: '#E5484D', redText: '#B42318', redTint: '#FDECEC', redBorder: '#F8D0D1',
};

const I = {
  home: <path d="M2.5 7.5 9 2.5l6.5 5v8h-13z M7 15.5v-5h4v5" />,
  plan: <><rect x="2.5" y="3.5" width="13" height="12" rx="3" /><path d="M2.5 7.5h13M9 10v3M7.5 11.5h3" /></>,
  list: <path d="M6.5 4.5h9M6.5 9h9M6.5 13.5h9M3 4.5h.01M3 9h.01M3 13.5h.01" />,
  search: <><circle cx="8" cy="8" r="5" /><path d="M11.8 11.8 15.5 15.5" /></>,
  scale: <path d="M3 5.5h12M3 9h8M3 12.5h5M13 11.5 15.5 14 13 16.5" />,
  calib: <><path d="M5 2.5v13M13 2.5v13" /><circle cx="5" cy="6.5" r="2" /><circle cx="13" cy="11.5" r="2" /></>,
  settings: <><circle cx="9" cy="9" r="2.6" /><path d="M9 2.2v1.8M9 14v1.8M15.8 9H14M4 9H2.2M13.8 4.2l-1.3 1.3M5.5 12.5l-1.3 1.3M13.8 13.8l-1.3-1.3M5.5 5.5 4.2 4.2" /></>,
};
const Icon = ({ d, size = 17 }: { d: ReactNode; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none' }}>{d}</svg>
);

const MAIN = [
  { key: '/', label: '홈', icon: I.home },
  { key: '/plans/new', label: '식단 생성', icon: I.plan },
  { key: '/plans', label: '식단 목록', icon: I.list },
];
const TOOLS = [
  { key: '/nutrition', label: '영양성분 검색', icon: I.search },
  { key: '/scaling', label: '레시피 스케일링', icon: I.scale },
  { key: '/calibration', label: '캘리브레이션', icon: I.calib },
];

const pageMeta: Record<string, { title: string; subtitle: string }> = {
  '/': { title: '홈', subtitle: '오늘 확인할 일과 진행 중인 식단' },
  '/plans': { title: '식단 목록', subtitle: '지난 식단을 열거나 복제해 다시 만들어요' },
  '/plans/new': { title: '식단 생성', subtitle: '조건을 입력하면 식단을 만들어 드려요' },
  '/nutrition': { title: '영양성분 검색', subtitle: '메뉴명으로 영양성분을 조회해요' },
  '/scaling': { title: '레시피 스케일링', subtitle: '1인분 레시피를 대량 조리량으로 변환해요' },
  '/calibration': { title: '캘리브레이션', subtitle: '현장 보정값을 기록해 정확도를 높여요' },
  '/settings': { title: '설정', subtitle: '계정과 기본값을 관리해요' },
};

const Avatar = () => (
  <div style={{ width: 30, height: 30, borderRadius: '50%', background: C.tint, color: C.greenText, font: '600 12px Pretendard,sans-serif', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
    {USER_NAME.slice(0, 1)}
  </div>
);

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [ddOpen, setDdOpen] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const ddRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const health = useHealth();
  // 사이드바 상태: 백엔드 연결 실패 / DB 연결 안 됨 / 정상. DB 가 꺼지면 식단 생성이 실패하므로 '정상'으로 두지 않는다.
  const bad = health === 'down' || health === 'db_down';
  const healthLabel = health === null ? '상태 확인 중' : health === 'down' ? '백엔드 연결 안 됨' : health === 'db_down' ? 'DB 연결 안 됨' : '백엔드 정상';
  // '새 식단' 비활성: 백엔드에 닿지 않을 때만(목업 스위치를 켠 개발 실행은 서버 없이도 화면을 볼 수 있다).
  const down = health === 'down' && !USE_MOCK;

  // 저장된 식단 상세(/plans/:id)는 메뉴상 '식단 목록' 아래로 본다.
  const isPlanDetail = /^\/plans\/\d+$/.test(pathname);
  const activeKey = isPlanDetail ? '/plans' : pathname;
  const meta = isPlanDetail
    ? { title: '식단 상세', subtitle: '저장된 식단을 읽기 전용으로 보여줘요' }
    : pageMeta[pathname] ?? { title: '', subtitle: '' };

  // 드롭다운 바깥을 누르면 닫는다
  useEffect(() => {
    if (!ddOpen) return;
    const onDown = (e: MouseEvent) => { if (ddRef.current && !ddRef.current.contains(e.target as Node)) setDdOpen(false); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [ddOpen]);

  const go = (to: string) => { setDdOpen(false); navigate(to); };

  const navItem = (k: string, label: string, icon: ReactNode) => {
    const on = activeKey === k;
    return (
      <div key={k} onClick={() => navigate(k)} onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)} title={collapsed ? label : undefined}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 11, alignSelf: collapsed ? 'center' : 'flex-start',
          height: layout.menuItemHeight, padding: collapsed ? 0 : '0 14px 0 11px', width: collapsed ? layout.menuItemHeight : undefined,
          justifyContent: collapsed ? 'center' : 'flex-start', borderRadius: layout.menuItemRadius, cursor: 'pointer',
          background: on ? C.tint : hover === k ? C.hover : 'transparent', color: on ? C.greenText : C.sub,
          font: `${on ? 600 : 400} 14px Pretendard,sans-serif`, whiteSpace: 'nowrap',
        }}>
        <Icon d={icon} />
        {!collapsed && <span>{label}</span>}
      </div>
    );
  };

  const ddItem = (key: string, label: string, onClick: () => void, danger = false) => (
    <div key={key} onClick={onClick} onMouseEnter={() => setHover(`dd-${key}`)} onMouseLeave={() => setHover(null)}
      style={{ height: 34, display: 'flex', alignItems: 'center', padding: '0 11px', borderRadius: 8, font: '400 13px Pretendard,sans-serif', cursor: 'pointer',
        color: danger ? C.redText : C.text, background: hover === `dd-${key}` ? (danger ? C.redTint : C.hover) : 'transparent' }}>{label}</div>
  );

  const sbWidth = collapsed ? layout.sidebarCollapsedWidth : layout.sidebarWidth;

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      {/* ───── 사이드바 ───── */}
      <aside style={{
        position: 'sticky', top: 0, height: '100vh', width: sbWidth, flex: 'none', transition: 'width .18s ease',
        background: 'rgba(255,255,255,0.72)', backdropFilter: 'blur(14px)', borderRight: `1px solid ${colors.border}`,
        display: 'flex', flexDirection: 'column', padding: '0 14px 16px', overflow: 'hidden',
      }}>
        <div style={{ height: 64, display: 'flex', alignItems: 'center', gap: 9, justifyContent: collapsed ? 'center' : 'flex-start', padding: collapsed ? 0 : '0 6px', cursor: 'pointer' }} onClick={() => navigate('/')}>
          <LogoMark size={24} />
          {!collapsed && <span style={{ font: '700 16px Pretendard,sans-serif', color: C.text, letterSpacing: '-0.02em' }}>OptiMeal</span>}
        </div>

        <nav style={{ display: 'flex', flexDirection: 'column', gap: 3, marginTop: 6 }}>
          {MAIN.map((m) => navItem(m.key, m.label, m.icon))}
        </nav>

        {/* 구분선 + "도구" 라벨 */}
        <div style={{ margin: '14px 0 8px', display: 'flex', alignItems: 'center', gap: 9, padding: collapsed ? 0 : '0 11px' }}>
          {!collapsed && <span style={{ font: '600 11px Pretendard,sans-serif', color: C.muted, letterSpacing: '0.06em' }}>도구</span>}
          <div style={{ flex: 1, height: 1, background: C.line }} />
        </div>

        <nav style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {TOOLS.map((m) => navItem(m.key, m.label, m.icon))}
        </nav>

        <div style={{ height: 22 }} />
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {navItem('/settings', '설정', I.settings)}
        </div>

        <div style={{ flex: 1 }} />

        {/* 하단 고정: 프로필 · 백엔드 상태 · 접기 */}
        <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: collapsed ? 0 : '0 4px', justifyContent: collapsed ? 'center' : 'flex-start' }}>
            <Avatar />
            {!collapsed && (
              <div style={{ minWidth: 0 }}>
                <div style={{ font: '600 13px Pretendard,sans-serif', color: C.text }}>{USER_NAME}</div>
                <div style={{ font: '400 11px Pretendard,sans-serif', color: C.faint, whiteSpace: 'nowrap' }}>{USER_ORG}</div>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 28, padding: collapsed ? 0 : '0 8px', borderRadius: 8, justifyContent: collapsed ? 'center' : 'flex-start', background: bad ? C.redTint : C.tint }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: bad ? C.redText : C.greenText, flex: 'none' }} />
            {!collapsed && <span style={{ font: '400 11px Pretendard,sans-serif', color: bad ? C.redText : C.greenText }}>{healthLabel}</span>}
          </div>
          <div onClick={() => setCollapsed((c) => !c)} onMouseEnter={() => setHover('fold')} onMouseLeave={() => setHover(null)}
            style={{ display: 'flex', alignItems: 'center', gap: 11, height: 30, padding: collapsed ? 0 : '0 8px', borderRadius: 8, justifyContent: collapsed ? 'center' : 'flex-start', color: C.sub, cursor: 'pointer', background: hover === 'fold' ? C.hover : 'transparent' }}>
            <svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none', transform: `rotate(${collapsed ? 180 : 0}deg)` }}><path d="M10.5 4.5 6 9l4.5 4.5" /></svg>
            {!collapsed && <span style={{ font: '400 12px Pretendard,sans-serif' }}>접기</span>}
          </div>
        </div>
      </aside>

      {/* ───── 본문 ───── */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <header style={{
          position: 'sticky', top: 0, zIndex: 10, height: layout.headerHeight, flex: 'none',
          background: 'rgba(255,255,255,0.62)', backdropFilter: 'blur(14px)', borderBottom: `1px solid ${colors.borderTop}`,
          display: 'flex', alignItems: 'center', gap: 16, padding: '0 28px',
        }}>
          <div style={{ minWidth: 0, lineHeight: 1.3 }}>
            <div style={{ font: '600 15px Pretendard,sans-serif', color: C.text, letterSpacing: '-0.01em' }}>{meta.title}</div>
            <div style={{ marginTop: 2, font: '400 12px Pretendard,sans-serif', color: C.faint }}>{meta.subtitle}</div>
          </div>
          <div style={{ flex: 1 }} />
          <button type="button" disabled={down} onClick={() => navigate('/plans/new')} onMouseEnter={() => setHover('new')} onMouseLeave={() => setHover(null)}
            style={{ display: 'flex', alignItems: 'center', gap: 7, height: 36, padding: '0 15px', border: 'none', borderRadius: 10,
              background: hover === 'new' && !down ? C.greenHover : C.green, color: '#fff', font: '600 13px Pretendard,sans-serif',
              cursor: down ? 'not-allowed' : 'pointer', opacity: down ? 0.45 : 1 }}>
            <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M7 2.5v9M2.5 7h9" /></svg>
            새 식단
          </button>
          <div style={{ width: 1, height: 22, background: '#E9EEEB' }} />

          {/* 프로필 + 드롭다운(시안 02a) */}
          <div ref={ddRef} style={{ position: 'relative' }}>
            <div onClick={() => setDdOpen((o) => !o)} onMouseEnter={() => setHover('me')} onMouseLeave={() => setHover(null)}
              style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer', padding: 3, borderRadius: 10, background: hover === 'me' || ddOpen ? C.hover : 'transparent' }}>
              <Avatar />
              <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke={C.muted} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3.5 5.5 7 9l3.5-3.5" /></svg>
            </div>
            {ddOpen && (
              <div style={{ position: 'absolute', right: -4, top: 46, width: 222, background: '#fff', border: `1px solid ${C.border}`, borderRadius: 14, padding: 7,
                boxShadow: '0 12px 32px rgba(22,33,28,0.12)', display: 'flex', flexDirection: 'column', gap: 1, zIndex: 20 }}>
                <div style={{ padding: '9px 11px 11px', borderBottom: `1px solid ${C.line}`, marginBottom: 5 }}>
                  <div style={{ font: '600 13px Pretendard,sans-serif', color: C.text }}>{USER_NAME}</div>
                  <div style={{ marginTop: 2, font: '400 12px Pretendard,sans-serif', color: C.faint }}>{USER_ORG}</div>
                </div>
                {ddItem('info', '내 정보', () => go('/settings'))}
                {ddItem('store', '소속 업장 변경', () => go('/settings'))}
                {ddItem('settings', '설정', () => go('/settings'))}
                <div style={{ height: 1, background: C.line, margin: '5px 0' }} />
                {ddItem('logout', '로그아웃', () => go('/login'), true)}
              </div>
            )}
          </div>
        </header>

        {USE_MOCK && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '10px 28px', background: colors.warningTint, borderBottom: `1px solid ${colors.warning}`, flex: 'none' }}>
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke={colors.warningText} strokeWidth="1.6" strokeLinecap="round"><circle cx="8" cy="8" r="6" /><path d="M8 5v3.5M8 11h.01" /></svg>
            <span style={{ font: '600 13px Pretendard,sans-serif', color: colors.warningText }}>{MOCK_BANNER} (VITE_USE_MOCK=true) · 저장·PDF·CSV 내려받기를 막았어요</span>
          </div>
        )}
        {bad && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '10px 28px', background: C.redTint, borderBottom: `1px solid ${C.redBorder}`, flex: 'none' }}>
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke={C.red} strokeWidth="1.6" strokeLinecap="round"><circle cx="8" cy="8" r="6" /><path d="M8 5v3.5M8 11h.01" /></svg>
            <span style={{ font: '400 13px Pretendard,sans-serif', color: C.redText }}>
              {health === 'down'
                ? '백엔드에 연결할 수 없어요 — 백엔드 서버(포트 8000)를 켜 주세요'
                : <>영양성분 DB에 연결할 수 없어요 — 식단 생성·영양성분 검색이 실패해요. <code>docker start optimeal_db</code> 로 DB를 켜 주세요</>}
            </span>
          </div>
        )}

        <main style={{ flex: 1, minHeight: 0, padding: layout.contentPadding, position: 'relative' }}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}