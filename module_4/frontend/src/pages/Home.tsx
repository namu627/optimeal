import { Card, Button, Tag, Space } from 'antd';
import { EditOutlined, FileTextOutlined, PlusOutlined, CalendarOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { colors } from '../theme';
import Gauge from '../components/Gauge';

// 진행 중인 식단 요약(홈 카드). 달성률은 목표 대비 %.
interface HomeCheck { level: 'error' | 'warning'; text: string; sub: string }
interface HomePlan {
  name: string; status: '초안' | '확정';
  target: string; people: number; days: string; meal: string; allergyGroups: number;
  budget: number; cost: number;
  calorie: number; protein: number; sodium: number;
  updatedAt: string;
  week: { day: string; date: string; menus: string[]; kcal: number; flag: string | null }[];
  checks: HomeCheck[];
}

// 최근 식단 조회 — 식단 저장·조회 API가 아직 없어 항상 null(빈 상태)을 돌려준다.
// 예전에는 시안용 가짜 식단('9월 2주차 · 초등학생 중식' 등)을 실제처럼 보여줬다.
// TODO: 식단 저장 API가 생기면 여기서 최근 초안/확정 식단을 불러온다.
function loadRecentPlan(): HomePlan | null {
  return null;
}

const todayText = new Date().toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });
const linkStyle = { fontSize: 12, color: colors.textSecondary, cursor: 'pointer' };

function TrayIllust({ size = 96 }: { size?: number }) {
  return (
    <svg width={size} height={size * 0.62} viewBox="0 0 150 94" fill="none">
      <rect x="1" y="8" width="78" height="78" rx="10" stroke="#BFE3CD" strokeWidth="2" />
      <path d="M40 8v78M1 47h78" stroke="#BFE3CD" strokeWidth="2" />
      <circle cx="20" cy="28" r="8" fill="#C9E9D6" />
      <circle cx="59" cy="28" r="8" fill="#DCEFC2" />
      <circle cx="20" cy="66" r="8" fill="#CFE7EE" />
      <path d="M48 74a11 11 0 0 1 22 0z" fill="#BFE3CD" />
      <path d="M97 86V44" stroke="#BFE3CD" strokeWidth="3" strokeLinecap="round" />
      <path d="M97 44c-9 0-14-7-14-16 0-9 5-16 14-16" stroke="#BFE3CD" strokeWidth="3" strokeLinecap="round" />
      <circle cx="120" cy="20" r="9" fill="#DCEFC2" />
      <circle cx="134" cy="44" r="7" fill="#C9E9D6" />
    </svg>
  );
}

function PersonTrayIllust() {
  return (
    <svg width="150" height="66" viewBox="0 0 150 66" fill="none">
      <circle cx="26" cy="18" r="9" stroke="#BFE3CD" strokeWidth="2" />
      <path d="M8 60c0-10 8-18 18-18s18 8 18 18" stroke="#BFE3CD" strokeWidth="2" strokeLinecap="round" />
      <rect x="62" y="6" width="62" height="54" rx="9" stroke="#BFE3CD" strokeWidth="2" />
      <path d="M93 6v54M62 33h62" stroke="#BFE3CD" strokeWidth="2" />
      <circle cx="77" cy="20" r="6" fill="#C9E9D6" />
      <circle cx="109" cy="20" r="6" fill="#DCEFC2" />
      <circle cx="77" cy="47" r="6" fill="#CFE7EE" />
      <circle cx="109" cy="47" r="6" fill="#C9E9D6" />
      <path d="M138 60V28" stroke="#BFE3CD" strokeWidth="3" strokeLinecap="round" />
      <circle cx="138" cy="18" r="7" fill="#DCEFC2" />
    </svg>
  );
}

export default function Home() {
  const navigate = useNavigate();
  const plan = loadRecentPlan();

  const confirmed = plan?.status === '확정';
  const checks = plan?.checks ?? [];

  const greetingSub = !plan
    ? '진행 중인 식단이 없어요. 새 식단을 만들어 보세요'
    : confirmed
    ? `${plan.name} 식단이 확정되었어요`
    : `확인이 필요한 항목이 ${checks.length}건 있어요`;

  const goPlans = () => navigate('/plans');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', padding: '20px 28px', borderRadius: 16, background: 'linear-gradient(90deg, #E9F7EF 0%, #EFF9F3 55%, #F4FBF7 100%)', border: `1px solid ${colors.border}` }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, color: colors.primaryActive }}>{todayText}</div>
          <div style={{ marginTop: 6, fontSize: 24, fontWeight: 700, color: colors.text }}>안녕하세요</div>
          <div style={{ marginTop: 6, fontSize: 13, color: colors.textSecondary }}>{greetingSub}</div>
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <PersonTrayIllust />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        {plan ? (
          <Card style={{ flex: 2, minWidth: 0 }} styles={{ body: { padding: 0 } }}>
            <div style={{ padding: '16px 20px 0' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ width: 26, height: 26, borderRadius: 8, background: colors.primaryTintSoft, color: colors.primaryActive, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13 }}>
                  <CalendarOutlined />
                </span>
                <span style={{ fontSize: 13, fontWeight: 600, color: colors.primaryActive }}>진행 중인 식단</span>
                <span style={{ ...linkStyle, marginLeft: 'auto' }} onClick={goPlans}>식단 목록 →</span>
              </div>

              <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 20, fontWeight: 700, color: colors.text }}>{plan.name}</span>
                <Tag color={confirmed ? 'success' : undefined}>{confirmed ? '확정' : plan.status}</Tag>
              </div>
              <div style={{ marginTop: 6, fontSize: 13, color: colors.textSecondary }}>
                {plan.target} · {plan.people}명 · {plan.days} · {plan.meal} · 알레르기 {plan.allergyGroups}그룹 · 예산 {plan.budget.toLocaleString()}원/식
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 20, padding: '18px 20px', marginTop: 16, borderTop: `1px solid ${colors.borderSubtle}` }}>
              <Gauge value={plan.calorie} label="열량" size={86} mode="band" />
              <Gauge value={plan.protein} label="단백질" size={86} mode="min" />
              <Gauge value={plan.sodium} label="나트륨" size={86} mode="max" />
              <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                <div style={{ fontSize: 12, color: colors.textSecondary }}>1인 원가</div>
                <div className="tabular" style={{ fontSize: 28, fontWeight: 700, color: colors.text, lineHeight: 1.2 }}>
                  {plan.cost.toLocaleString()}
                  <span style={{ fontSize: 14, fontWeight: 600, marginLeft: 2 }}>원</span>
                </div>
                <div style={{ marginTop: 6, display: 'inline-block', padding: '3px 10px', borderRadius: 8, background: colors.primaryTintSoft, color: colors.primaryActive, fontSize: 12, fontWeight: 600 }}>
                  예산 {plan.budget.toLocaleString()}원 내 · −{(plan.budget - plan.cost).toLocaleString()}원
                </div>
              </div>
            </div>

            <div style={{ padding: '16px 20px', borderTop: `1px solid ${colors.borderSubtle}` }}>
              <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: colors.text }}>이번 주 메뉴</span>
                <span style={{ ...linkStyle, marginLeft: 'auto' }}>식단표 전체 →</span>
              </div>

              <div style={{ display: 'flex', gap: 8 }}>
                {plan.week.map((d) => (
                  <div key={d.day} style={{ flex: 1, minWidth: 0, border: `1px solid ${d.flag ? '#F6D2C2' : colors.borderSubtle}`, background: d.flag ? colors.errorTint : '#fff', borderRadius: 10, padding: '10px 12px' }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 5, marginBottom: 6 }}>
                      <span style={{ fontSize: 12, fontWeight: 700, color: colors.text }}>{d.day}</span>
                      <span style={{ fontSize: 11, color: colors.textTertiary }}>{d.date}</span>
                    </div>
                    {d.menus.map((m) => (
                      <div key={m} style={{ fontSize: 12, color: colors.text, lineHeight: 1.75 }}>{m}</div>
                    ))}
                    <div className="tabular" style={{ marginTop: 8, fontSize: 11, fontWeight: 600, color: colors.textTertiary }}>{d.kcal} kcal</div>
                    {d.flag && <div style={{ marginTop: 4, fontSize: 11, fontWeight: 600, color: colors.errorText }}>{d.flag}</div>}
                  </div>
                ))}
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', padding: '14px 20px 18px', borderTop: `1px solid ${colors.borderSubtle}` }}>
              <Space>
                <Button type="primary" icon={<EditOutlined />}>이어서 편집하기</Button>
                <Button icon={<FileTextOutlined />}>식단표 보기</Button>
              </Space>
              <span style={{ marginLeft: 'auto', fontSize: 12, color: colors.textTertiary }}>
                {confirmed ? `확정 ${plan.updatedAt}` : `마지막 수정 ${plan.updatedAt}`}
              </span>
            </div>
          </Card>
        ) : (
          <Card style={{ flex: 2, minWidth: 0 }} styles={{ body: { padding: '64px 20px' } }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16 }}>
              <TrayIllust size={130} />
              <div style={{ fontSize: 18, fontWeight: 700, color: colors.text }}>아직 진행 중인 식단이 없어요</div>
              <div style={{ fontSize: 13, color: colors.textSecondary }}>대상·기간·예산을 입력하면 조건을 만족하는 식단을 만들어 드려요</div>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/plans/new')}>첫 식단 만들기</Button>
            </div>
          </Card>
        )}

        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card styles={{ body: { padding: '16px 20px' } }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ width: 26, height: 26, borderRadius: 8, background: colors.errorTint, color: colors.errorText, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13 }}>
                <ExclamationCircleOutlined />
              </span>
              <span style={{ fontSize: 14, fontWeight: 700, color: colors.text }}>확인 필요</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: checks.length ? colors.error : colors.textTertiary }}>{checks.length}</span>
            </div>

            <div style={{ marginTop: 6 }}>
              {!checks.length && (
                <div style={{ padding: '12px 0 4px', fontSize: 13, color: colors.textTertiary }}>확인할 항목이 없어요</div>
              )}
              {checks.map((c) => (
                <div key={c.text} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '12px 0', borderBottom: `1px solid ${colors.borderSubtle}` }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', marginTop: 6, background: c.level === 'error' ? colors.error : colors.warning, flex: 'none' }} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: colors.text }}>{c.text}</div>
                    <div style={{ marginTop: 3, fontSize: 12, color: colors.textTertiary }}>{c.sub}</div>
                  </div>
                  <span style={{ ...linkStyle, marginLeft: 'auto', whiteSpace: 'nowrap' }}>보기 →</span>
                </div>
              ))}
            </div>
          </Card>

          <Card styles={{ body: { padding: 20 } }}>
            <div style={{ display: 'flex', alignItems: 'flex-start' }}>
              <div style={{ flex: 1, paddingRight: 8 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: colors.text }}>새 식단 만들기</div>
                <div style={{ marginTop: 8, fontSize: 13, color: colors.textSecondary, lineHeight: 1.6 }}>대상·기간·예산을 입력하면 조건을 만족하는 초안을 만들어 드려요</div>
              </div>
              <TrayIllust size={76} />
            </div>
            <Button type="primary" icon={<PlusOutlined />} style={{ marginTop: 16, width: '100%', height: 40 }} onClick={() => navigate('/plans/new')}>
              식단 생성 시작
            </Button>
          </Card>
        </div>
      </div>
    </div>
  );
}