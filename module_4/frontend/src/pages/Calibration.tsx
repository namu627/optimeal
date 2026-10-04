// src/pages/Calibration.tsx
// 화면 9 · 캘리브레이션 (시안 09 기본 · 09a 업장 등록 모달 · 09b 이력 없음)
// 흐름: 업장 선택(GET /api/calibration/sites) → 레시피·인원수 선택
//       → 시스템 제안량(POST /api/scaling/predict, 그 업장 보정 반영) 옆에 실제 사용량 입력
//       → 보정 저장(POST /api/calibration/observations, 재료마다 1건) → 수렴 곡선·참고 이력 갱신
// 주의(ADR-008): 첫 회차는 선형(1인분×인원수)이 제안량이다. 수렴 곡선은 3회차 미만이면 추세로 읽지 않는다.
//               CBR 참고 이력은 표시 전용이며 제안량에 자동 적용되지 않는다.
import { useEffect, useMemo, useRef, useState } from 'react';
import { App, Button, Card, Empty, Input, InputNumber, Modal, Select, Spin } from 'antd';
import { PlusOutlined, InfoCircleOutlined, LineChartOutlined, HomeOutlined } from '@ant-design/icons';
import {
  listRecipes, listSites, predictScaling,
  type RecipeListItem, type ScalingResponse, type SiteItem,
} from '../api/scaling';
import {
  createSite, getConvergence, getReferences, recordObservation, nextSiteId,
  getSiteHeadcount, setSiteHeadcount,
  type CbrReference, type ConvergenceOut,
} from '../api/calibration';
import { colors, radius } from '../theme';

// 업장 유형 — 백엔드는 자유 입력 문자열로 저장만 한다(계산에 쓰이지 않음). 급식 대상 프로파일 구분에 맞춘 목록.
const SITE_TYPES = ['초등학교', '중학교', '고등학교', '대학교', '산업체', '노인복지관', '병원', '군부대'];
const WARN_PCT = 10;        // 제안량 대비 ±10% 초과는 저장 전에 한 번 더 확인
const DEFAULT_HEADCOUNT = 100;
const MAX_STATUS_SITES = 12; // 업장별 현황은 업장마다 1회 조회하므로 상한을 둔다
const MAX_CBR_ROWS = 8;

const NUM: React.CSSProperties = { fontVariantNumeric: 'tabular-nums' };
const CARD: React.CSSProperties = { borderRadius: radius.card };
const GRID_INPUT = 'minmax(0,1fr) 120px 132px 84px';
const GRID_CBR = 'minmax(0,1.1fr) minmax(0,1.1fr) 56px minmax(0,1fr) 70px 64px 56px';

interface Keyed<T> { key: string; data: T }
interface RoundPoint { round: number; pct: number }
interface SiteStatus { site_id: number; total: number; cold: number; rounds: number }
interface CbrRow {
  key: string; siteName: string; recipeName: string; rounds: number;
  ingredientName: string; pct: number; date: string; jaccard: number;
}

const round1 = (v: number) => Math.round(v * 10) / 10;
const signed = (v: number) => { const r = round1(v); return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(1)}%`; };
const mmdd = (iso?: string | null) => {
  if (!iso) return '–';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '–' : `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
};

/** 재료별 수렴 곡선 → 회차별 평균(재료 평균). 실측 0g 등으로 값이 없는 점은 뺀다. */
function averageByRound(curves: ConvergenceOut[]): RoundPoint[] {
  const acc = new Map<number, { sum: number; n: number }>();
  curves.forEach((c) => c.points.forEach((p) => {
    if (p.ape_pct == null || !Number.isFinite(p.ape_pct)) return;
    const a = acc.get(p.round_no) ?? { sum: 0, n: 0 };
    acc.set(p.round_no, { sum: a.sum + p.ape_pct, n: a.n + 1 });
  }));
  return [...acc.entries()].sort((a, b) => a[0] - b[0]).map(([round, a]) => ({ round, pct: a.sum / a.n }));
}

/** 수렴 곡선 — 프로젝트의 유일한 선 차트. 선 1개 · 격자 최소 · 점마다 값 라벨. */
function ConvergenceChart({ points }: { points: RoundPoint[] }) {
  const W = 520, H = 240, L = 40, R = 24, T = 26, B = 34;
  const max = Math.max(10, Math.ceil(Math.max(...points.map((p) => p.pct)) / 10) * 10);
  const x = (i: number) => (points.length === 1 ? (L + W - R) / 2 : L + ((W - L - R) * i) / (points.length - 1));
  const y = (v: number) => T + (H - T - B) * (1 - Math.min(v, max) / max);
  const ticks = [0.25, 0.5, 0.75, 1].map((f) => max * f);
  const every = Math.ceil(points.length / 8); // 회차가 많으면 라벨을 솎는다
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.pct).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="회차별 평균 보정량 곡선" style={{ display: 'block' }}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke={colors.border} strokeWidth={1} />
          <text x={L - 8} y={y(t) + 3} textAnchor="end" fontSize={10} fill={colors.textTertiary}>{Math.round(t)}</text>
        </g>
      ))}
      {points.length > 1 && <path d={path} fill="none" stroke={colors.primary} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
      {points.map((p, i) => {
        const last = i === points.length - 1;
        const show = last || i % every === 0;
        return (
          <g key={p.round}>
            <circle cx={x(i)} cy={y(p.pct)} r={4} fill={last ? colors.primary : colors.bgContainer} stroke={colors.primary} strokeWidth={2} />
            {show && (
              <>
                <text x={x(i)} y={y(p.pct) - 10} textAnchor="middle" fontSize={10} fontWeight={last ? 700 : 400}
                  fill={last ? colors.primaryActive : colors.textSecondary}>{Math.round(p.pct)}%</text>
                <text x={x(i)} y={H - 10} textAnchor="middle" fontSize={10} fill={colors.textTertiary}>{p.round}회차</text>
              </>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function Chip({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'green' | 'warn' }) {
  const s = tone === 'green' ? { c: colors.primaryActive, bg: colors.primaryTintSoft }
    : tone === 'warn' ? { c: colors.errorText, bg: colors.errorTint }
      : { c: colors.textSecondary, bg: colors.borderSubtle };
  return (
    <span style={{ ...NUM, display: 'inline-flex', alignItems: 'center', height: 26, padding: '0 10px', borderRadius: radius.badge,
      background: s.bg, color: s.c, fontSize: 12, fontWeight: tone === 'neutral' ? 400 : 600, whiteSpace: 'nowrap' }}>
      {children}
    </span>
  );
}

export default function Calibration() {
  const { message, modal } = App.useApp();

  const [sites, setSites] = useState<SiteItem[] | null>(null); // null = 불러오는 중
  const [recipes, setRecipes] = useState<RecipeListItem[]>([]);
  const [recipesLoading, setRecipesLoading] = useState(true);
  const [siteId, setSiteId] = useState<number | undefined>(undefined);
  const [recipeKey, setRecipeKey] = useState<string | undefined>(undefined);
  const [headcount, setHeadcount] = useState<number>(DEFAULT_HEADCOUNT);
  const [tick, setTick] = useState(0); // 저장 뒤 다시 불러오기용
  const [saving, setSaving] = useState(false);

  // 업장 등록 모달(시안 09a)
  const [regOpen, setRegOpen] = useState(false);
  const [regName, setRegName] = useState('');
  const [regType, setRegType] = useState(SITE_TYPES[0]);
  const [regCount, setRegCount] = useState<number | null>(null);
  const [regSaving, setRegSaving] = useState(false);

  const firstInput = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    listSites()
      .then((list) => {
        if (!alive) return;
        setSites(list);
        if (list.length) {
          setSiteId((prev) => prev ?? list[0].site_id);
          setHeadcount(getSiteHeadcount(list[0].site_id) ?? DEFAULT_HEADCOUNT);
        }
      })
      .catch(() => { if (alive) setSites([]); });
    // 레시피는 205개뿐이라 한 번에 받아 이름 검색을 프론트에서 처리(스케일링 화면과 같은 방식)
    listRecipes('', 500)
      .then((list) => { if (alive) setRecipes(list); })
      .catch(() => undefined)
      .finally(() => { if (alive) setRecipesLoading(false); });
    return () => { alive = false; };
  }, []);

  const recipeNameById = useMemo(
    () => Object.fromEntries(recipes.map((r) => [r.recipe_id, r.recipe_name?.trim() || r.recipe_key])),
    [recipes],
  );
  const recipeOptions = useMemo(
    () => recipes.map((r) => ({
      value: r.recipe_key,
      label: r.recipe_name?.trim() || r.recipe_key,
      search: `${r.recipe_name ?? ''} ${r.recipe_key} ${r.cooking_method}`.toLowerCase(),
    })),
    [recipes],
  );

  // ── 시스템 제안량: 선택한 업장의 보정이 반영된 스케일링 결과 ──────────────────
  const predKey = siteId && recipeKey && headcount >= 1 ? `${siteId}|${recipeKey}|${headcount}|${tick}` : '';
  const [pred, setPred] = useState<Keyed<ScalingResponse | null> | null>(null);
  useEffect(() => {
    if (!predKey || !siteId || !recipeKey) return;
    let alive = true;
    predictScaling({ recipe_key: recipeKey, n_target: headcount, site_id: siteId })
      .then((data) => { if (alive) setPred({ key: predKey, data }); })
      .catch(() => { if (alive) setPred({ key: predKey, data: null }); });
    return () => { alive = false; };
  }, [predKey, siteId, recipeKey, headcount]);
  const current = pred && pred.key === predKey ? pred.data : null;
  const predLoading = !!predKey && pred?.key !== predKey;
  const predFailed = !!predKey && pred?.key === predKey && pred.data === null;

  // 실제 사용량 입력값 — 제안량이 바뀌면(업장·레시피·인원수·저장) 처음부터 다시 받는다.
  const [edits, setEdits] = useState<Keyed<Record<number, number | null>>>({ key: '', data: {} });
  const actualOf = (id: number, suggested: number): number | null =>
    edits.key === predKey && id in edits.data ? edits.data[id] : Math.round(suggested);
  const setActual = (id: number, v: number | null) =>
    setEdits((prev) => ({ key: predKey, data: { ...(prev.key === predKey ? prev.data : {}), [id]: v } }));

  const rows = useMemo(() => (current?.ingredients ?? []).map((ing) => {
    const actual = edits.key === predKey && ing.ingredient_id in edits.data ? edits.data[ing.ingredient_id] : Math.round(ing.scaled_g);
    // 화면에 보이는 제안량(g 단위 반올림) 기준으로 차이를 낸다 — 손대지 않은 칸은 정확히 0%
    const suggested = Math.round(ing.scaled_g);
    const diff = actual == null || suggested <= 0 ? null : ((actual - suggested) / suggested) * 100;
    return { ing, actual, diff, flagged: diff != null && Math.abs(diff) > WARN_PCT };
  }), [current, edits, predKey]);

  const doneRounds = current ? Math.max(0, ...current.ingredients.map((i) => i.n_obs)) : 0;
  const recipeId = current?.recipe_id;
  const ingKey = current ? current.ingredients.map((i) => i.ingredient_id).join(',') : '';

  // ── 수렴 곡선 + CBR 참고 이력 (재료마다 조회) ─────────────────────────────────
  const detailKey = siteId && recipeId && ingKey ? `${siteId}|${recipeId}|${ingKey}|${tick}` : '';
  const [detail, setDetail] = useState<Keyed<{ curves: ConvergenceOut[]; refs: { id: number; list: CbrReference[] }[] }> | null>(null);
  useEffect(() => {
    if (!detailKey || !siteId || !recipeId) return;
    let alive = true;
    const ids = ingKey.split(',').map(Number);
    Promise.all([
      Promise.all(ids.map((id) => getConvergence(siteId, recipeId, id))),
      Promise.all(ids.map((id) => getReferences(id, ids).then((r) => ({ id, list: r.references })))),
    ])
      .then(([curves, refs]) => { if (alive) setDetail({ key: detailKey, data: { curves, refs } }); })
      .catch(() => { if (alive) setDetail({ key: detailKey, data: { curves: [], refs: [] } }); });
    return () => { alive = false; };
  }, [detailKey, siteId, recipeId, ingKey]);
  const detailNow = detail && detail.key === detailKey ? detail.data : null;
  const detailLoading = !!detailKey && detail?.key !== detailKey;

  const curve = useMemo(() => averageByRound(detailNow?.curves ?? []), [detailNow]);
  const caveat = detailNow?.curves[0]?.caveat ?? '';

  const cbrRows = useMemo<CbrRow[]>(() => {
    if (!detailNow || !current) return [];
    const nameOf = Object.fromEntries(current.ingredients.map((i) => [i.ingredient_id, i.ingredient_name]));
    return detailNow.refs
      .flatMap(({ id, list }) => list
        // 지금 입력 중인 칸(같은 업장·같은 레시피) 자신은 참고 이력이 아니다
        .filter((r) => !(r.site_id === siteId && r.recipe_id === recipeId))
        .map((r) => ({
          key: `${r.site_id}-${r.recipe_id}-${id}`,
          siteName: sites?.find((s) => s.site_id === r.site_id)?.site_name || `업장 ${r.site_id}`,
          recipeName: recipeNameById[r.recipe_id] ?? `레시피 ${r.recipe_id}`,
          rounds: r.n_obs, ingredientName: nameOf[id] ?? `재료 ${id}`,
          pct: (r.est_ratio - 1) * 100, date: mmdd(r.updated_at), jaccard: r.jaccard,
        })))
      .sort((a, b) => b.jaccard - a.jaccard || b.rounds - a.rounds)
      .slice(0, MAX_CBR_ROWS);
  }, [detailNow, current, siteId, recipeId, sites, recipeNameById]);

  // ── 업장별 보정 현황 (선택한 레시피 기준) ─────────────────────────────────────
  const statusSites = useMemo(() => (sites ?? []).slice(0, MAX_STATUS_SITES), [sites]);
  const statusKey = recipeKey && statusSites.length ? `${recipeKey}|${statusSites.map((s) => s.site_id).join(',')}|${tick}` : '';
  const [status, setStatus] = useState<Keyed<SiteStatus[]> | null>(null);
  useEffect(() => {
    if (!statusKey || !recipeKey) return;
    let alive = true;
    Promise.all(statusSites.map((s) =>
      predictScaling({ recipe_key: recipeKey, n_target: 1, site_id: s.site_id }).then((r): SiteStatus => ({
        site_id: s.site_id, total: r.ingredients.length, cold: r.cold_start_count,
        rounds: Math.max(0, ...r.ingredients.map((i) => i.n_obs)),
      }))))
      .then((data) => { if (alive) setStatus({ key: statusKey, data }); })
      .catch(() => { if (alive) setStatus({ key: statusKey, data: [] }); });
    return () => { alive = false; };
  }, [statusKey, recipeKey, statusSites]);
  const statusNow = status && status.key === statusKey ? status.data : null;

  // ── 동작 ──────────────────────────────────────────────────────────────────
  const onSiteChange = (id: number) => {
    setSiteId(id);
    setHeadcount(getSiteHeadcount(id) ?? DEFAULT_HEADCOUNT);
  };

  const save = async () => {
    if (!current || !siteId) return;
    const targets = rows.filter((r) => r.actual != null && r.ing.base_amount_g > 0);
    if (!targets.length) { message.warning('실제 사용량을 입력해 주세요'); return; }
    setSaving(true);
    let ok = 0;
    try {
      // 한 회차 = 이 레시피의 재료 전부. 원장이 SQLite 라 차례로 보낸다.
      for (const r of targets) {
        await recordObservation({
          site_id: siteId, recipe_id: current.recipe_id, ingredient_id: r.ing.ingredient_id,
          n_target: current.n_target, base_amount_g: r.ing.base_amount_g, corrected_g: r.actual as number,
          cbr_shown: cbrRows.length > 0,
        });
        ok += 1;
      }
      message.success(`${doneRounds + 1}회차 보정을 저장했어요 (재료 ${ok}종)`);
    } catch {
      message.error(ok ? `재료 ${ok}종만 저장됐어요. 나머지는 다시 저장해 주세요` : '보정을 저장하지 못했어요');
    } finally {
      setSaving(false);
      if (ok) setTick((t) => t + 1);
    }
  };

  const onSaveClick = () => {
    const flagged = rows.filter((r) => r.flagged);
    if (!flagged.length) { void save(); return; }
    modal.confirm({
      title: `제안량과 ±${WARN_PCT}% 넘게 차이 나는 재료가 ${flagged.length}종 있어요`,
      content: (
        <div style={{ ...NUM, color: colors.textSecondary, fontSize: 13, lineHeight: '22px' }}>
          {flagged.map((r) => <div key={r.ing.ingredient_id}>{r.ing.ingredient_name} {signed(r.diff as number)}</div>)}
          <div style={{ marginTop: 6 }}>입력값이 맞다면 그대로 저장하세요.</div>
        </div>
      ),
      okText: '그대로 저장', cancelText: '다시 확인',
      onOk: save,
    });
  };

  const openRegister = () => {
    setRegName(''); setRegType(SITE_TYPES[0]); setRegCount(null); setRegOpen(true);
  };

  const register = async () => {
    const name = regName.trim();
    if (!name) { message.warning('업장명을 입력해 주세요'); return; }
    if ((sites ?? []).some((s) => s.site_name === name)) { message.warning('같은 이름의 업장이 이미 있어요'); return; }
    setRegSaving(true);
    try {
      // 다른 화면에서 그사이 등록됐을 수 있으므로 최신 목록으로 id 를 정한다
      const latest = await listSites();
      const id = nextSiteId(latest);
      await createSite({ site_id: id, site_name: name, site_type: regType });
      if (regCount && regCount >= 1) setSiteHeadcount(id, regCount);
      setSites(await listSites());
      setSiteId(id);
      setHeadcount(regCount && regCount >= 1 ? regCount : DEFAULT_HEADCOUNT);
      setRegOpen(false);
      message.success(`'${name}' 업장을 등록했어요`);
    } catch {
      message.error('업장을 등록하지 못했어요');
    } finally {
      setRegSaving(false);
    }
  };

  // ── 화면 ──────────────────────────────────────────────────────────────────
  const registerModal = (
    <Modal open={regOpen} onCancel={() => setRegOpen(false)} onOk={register} okText="등록" cancelText="취소"
      confirmLoading={regSaving} width={440} centered
      title={(
        <div>
          <div style={{ fontSize: 17, fontWeight: 700, color: colors.text }}>업장 등록</div>
          <div style={{ fontSize: 12, fontWeight: 400, color: colors.textTertiary, marginTop: 4 }}>등록 후 첫 회차는 선형 기준으로 시작합니다</div>
        </div>
      )}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: '10px 0 6px' }}>
        <label>
          <div style={{ fontSize: 12, color: colors.textSecondary, marginBottom: 6 }}>업장명</div>
          <Input value={regName} onChange={(e) => setRegName(e.target.value)} onPressEnter={register} maxLength={100}
            placeholder="예: 서울고등학교" autoFocus />
        </label>
        <label>
          <div style={{ fontSize: 12, color: colors.textSecondary, marginBottom: 6 }}>유형</div>
          <Select value={regType} onChange={setRegType} style={{ width: '100%' }}
            options={SITE_TYPES.map((t) => ({ value: t, label: t }))} />
          <div style={{ fontSize: 12, color: colors.textTertiary, marginTop: 6 }}>{SITE_TYPES.join(' · ')}</div>
        </label>
        <label>
          <div style={{ fontSize: 12, color: colors.textSecondary, marginBottom: 6 }}>기준 인원</div>
          <InputNumber value={regCount} onChange={(v) => setRegCount(v == null ? null : Number(v))} min={1} max={5000}
            suffix="명" style={{ width: 180 }} placeholder="예: 480" />
          <div style={{ fontSize: 12, color: colors.textTertiary, marginTop: 6 }}>보정값 입력의 기본 인원수로 쓰여요</div>
        </label>
      </div>
    </Modal>
  );

  if (sites === null) {
    return <div style={{ display: 'grid', placeItems: 'center', minHeight: 320 }}><Spin /></div>;
  }

  if (sites.length === 0) {
    return (
      <>
        <Card style={CARD} styles={{ body: { padding: 48 } }}>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={(
              <div>
                <div style={{ color: colors.text, fontWeight: 600 }}>등록된 업장이 없어요</div>
                <div style={{ color: colors.textSecondary, fontSize: 13, marginTop: 4 }}>업장을 등록하면 그 업장의 보정값을 회차마다 쌓을 수 있어요</div>
              </div>
            )}>
            <Button type="primary" icon={<PlusOutlined />} onClick={openRegister}>업장 등록</Button>
          </Empty>
        </Card>
        {registerModal}
      </>
    );
  }

  const last = curve[curve.length - 1];
  const perRound = curve.length > 1 ? (last.pct - curve[0].pct) / (curve.length - 1) : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* 업장 선택 바 */}
      <Card style={CARD} styles={{ body: { padding: '12px 18px' } }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: colors.textSecondary }}>업장</span>
          <Select value={siteId} onChange={onSiteChange} style={{ width: 260 }}
            options={sites.map((s) => ({ value: s.site_id, label: s.site_type ? `${s.site_name} · ${s.site_type}` : s.site_name }))} />
          {current && (doneRounds > 0
            ? <Chip>이 레시피 {doneRounds}회차 보정됨</Chip>
            : <Chip>이 레시피 보정 이력 없음</Chip>)}
          <div style={{ flex: 1 }} />
          <Button icon={<PlusOutlined />} onClick={openRegister}>업장 등록</Button>
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: 16, alignItems: 'start' }}>
        {/* 보정값 입력 */}
        <Card style={CARD} styles={{ body: { padding: 20 } }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: colors.text, marginRight: 'auto' }}>보정값 입력</div>
            <Select showSearch value={recipeKey} onChange={setRecipeKey} loading={recipesLoading}
              placeholder="레시피 검색" style={{ width: 200 }} options={recipeOptions}
              filterOption={(input, option) => ((option as { search?: string })?.search ?? '').includes(input.trim().toLowerCase())}
              notFoundContent={recipesLoading ? '불러오는 중…' : '결과 없음'} />
            <InputNumber value={headcount} min={1} max={5000} suffix="명" style={{ width: 110 }}
              onChange={(v) => { if (v != null && Number(v) >= 1) setHeadcount(Math.round(Number(v))); }} />
            {current && <Chip tone="green">{doneRounds + 1}회차</Chip>}
          </div>

          {!recipeKey ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ margin: '28px 0' }}
              description={<span style={{ color: colors.textSecondary, fontSize: 13 }}>레시피를 고르면 재료별 시스템 제안량이 표시돼요</span>} />
          ) : predLoading ? (
            <div style={{ display: 'grid', placeItems: 'center', minHeight: 180 }}><Spin /></div>
          ) : predFailed || !current ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ margin: '28px 0' }}
              description={<span style={{ color: colors.textSecondary, fontSize: 13 }}>제안량을 불러오지 못했어요</span>}>
              <Button onClick={() => setTick((t) => t + 1)}>다시 시도</Button>
            </Empty>
          ) : (
            <>
              <div style={{ border: `1px solid ${colors.border}`, borderRadius: radius.button, overflow: 'hidden' }}>
                <div style={{ display: 'grid', gridTemplateColumns: GRID_INPUT, gap: 12, alignItems: 'center', padding: '10px 14px',
                  background: colors.bgLayout, fontSize: 12, color: colors.textSecondary }}>
                  <span>재료명</span>
                  <span style={{ textAlign: 'right' }}>시스템 제안(g)</span>
                  <span style={{ textAlign: 'right' }}>실제 사용(g)</span>
                  <span style={{ textAlign: 'right' }}>차이</span>
                </div>
                {rows.map(({ ing, diff, flagged }, i) => (
                  <div key={ing.ingredient_id} ref={i === 0 ? firstInput : undefined}
                    style={{ display: 'grid', gridTemplateColumns: GRID_INPUT, gap: 12, alignItems: 'center', padding: '8px 14px',
                      borderTop: `1px solid ${colors.borderSubtle}`, fontSize: 14 }}>
                    <span style={{ color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ing.ingredient_name}</span>
                    <span style={{ ...NUM, textAlign: 'right', color: colors.textSecondary }}>{Math.round(ing.scaled_g).toLocaleString()}</span>
                    <InputNumber value={actualOf(ing.ingredient_id, ing.scaled_g)} min={0} controls={false}
                      onChange={(v) => setActual(ing.ingredient_id, v == null ? null : Number(v))}
                      formatter={(v) => (v == null || String(v) === '' ? '' : Number(v).toLocaleString())}
                      // 칸을 비우면 0 이 아니라 '미입력'으로 둔다(0g 으로 잘못 저장되지 않게)
                      parser={(v) => (v && /\d/.test(v) ? Number(v.replace(/[^\d.]/g, '')) : ('' as unknown as number))}
                      aria-label={`${ing.ingredient_name} 실제 사용량(g)`}
                      style={{ ...NUM, width: '100%', ...(flagged ? { background: colors.errorTint, borderColor: colors.error } : {}) }}
                      styles={{ input: { textAlign: 'right' } }} />
                    <span style={{ textAlign: 'right' }}>
                      {diff == null ? <span style={{ color: colors.textTertiary }}>–</span>
                        : flagged ? <Chip tone="warn">{signed(diff)}</Chip>
                          : <span style={{ ...NUM, color: colors.textSecondary }}>{signed(diff)}</span>}
                    </span>
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 14 }}>
                <span style={{ width: 12, height: 12, borderRadius: 3, background: colors.errorTint, border: `1px solid ${colors.error}`, flex: 'none' }} />
                <span style={{ fontSize: 12, color: colors.textTertiary, marginRight: 'auto' }}>증감 방향과 무관하게 ±{WARN_PCT}% 초과는 확인 필요</span>
                <Button type="primary" loading={saving} onClick={onSaveClick}>보정 저장</Button>
              </div>
            </>
          )}
        </Card>

        {/* 수렴 곡선 — 이 화면에서 배경에 색을 쓰는 단 하나의 카드(컨벤션 §2) */}
        <Card style={{ ...CARD, background: colors.primaryTintSoft, borderColor: colors.primaryTint }} styles={{ body: { padding: 20 } }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <span style={{ width: 30, height: 30, borderRadius: '50%', background: colors.primaryTint, color: colors.primaryActive,
              display: 'grid', placeItems: 'center', flex: 'none' }}><LineChartOutlined /></span>
            <span style={{ fontSize: 14, fontWeight: 600, color: colors.text, marginRight: 'auto' }}>수렴 곡선</span>
            <span style={{ fontSize: 12, color: colors.textSecondary }}>평균 보정량 (%)</span>
          </div>
          {detailLoading ? (
            <div style={{ display: 'grid', placeItems: 'center', minHeight: 220 }}><Spin /></div>
          ) : curve.length === 0 ? (
            <div style={{ background: colors.bgContainer, borderRadius: radius.button, padding: '44px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 15, fontWeight: 600, color: colors.text }}>아직 보정 이력이 없어요</div>
              <div style={{ fontSize: 13, color: colors.textSecondary, margin: '8px 0 16px' }}>첫 회차는 선형 기준으로 시작합니다</div>
              <Button disabled={!current} onClick={() => firstInput.current?.querySelector('input')?.focus()}>1회차 보정값 입력</Button>
            </div>
          ) : (
            <>
              <ConvergenceChart points={curve} />
              <div style={{ ...NUM, fontSize: 13, color: colors.textSecondary, marginTop: 8 }}>
                {last.round}회차 기준 평균 보정량 {Math.round(last.pct)}%
                {perRound != null && ` · 회차당 평균 ${perRound > 0 ? '+' : perRound < 0 ? '−' : ''}${Math.abs(round1(perRound))}%p ${perRound > 0 ? '증가' : '감소'}`}
              </div>
              {curve.length < 3 && (
                <div style={{ fontSize: 12, color: colors.textTertiary, marginTop: 4 }} title={caveat}>
                  3회차 미만은 추세로 해석하지 않습니다
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start' }}>
        {/* 업장별 보정 현황 */}
        <Card style={{ ...CARD, flex: '1 1 320px', minWidth: 0 }} styles={{ body: { padding: 20 } }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            <span style={{ width: 30, height: 30, borderRadius: '50%', background: colors.borderSubtle, color: colors.teal,
              display: 'grid', placeItems: 'center', flex: 'none' }}><HomeOutlined /></span>
            <span style={{ fontSize: 14, fontWeight: 600, color: colors.text, marginRight: 'auto' }}>업장별 보정 현황</span>
            <span style={{ fontSize: 12, color: colors.textSecondary }}>선형 추정 재료 비율</span>
          </div>
          {!recipeKey ? (
            <div style={{ fontSize: 13, color: colors.textTertiary, padding: '18px 0' }}>레시피를 고르면 업장마다 보정이 얼마나 쌓였는지 보여줘요</div>
          ) : !statusNow ? (
            <div style={{ display: 'grid', placeItems: 'center', minHeight: 120 }}><Spin /></div>
          ) : (
            statusSites.map((s, i) => {
              const st = statusNow.find((x) => x.site_id === s.site_id);
              const pct = st && st.total ? Math.round((st.cold / st.total) * 100) : null;
              return (
                <div key={s.site_id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 2px',
                  borderTop: i ? `1px solid ${colors.borderSubtle}` : 'none' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, color: colors.text, fontWeight: s.site_id === siteId ? 600 : 400 }}>{s.site_name || `업장 ${s.site_id}`}</div>
                    <div style={{ ...NUM, fontSize: 12, color: colors.textTertiary }}>
                      {st ? (st.rounds > 0 ? `${st.rounds}회차 보정` : '보정 이력 없음') : '조회 실패'}
                    </div>
                  </div>
                  {pct != null && <Chip tone={!st || st.rounds === 0 ? 'neutral' : pct <= WARN_PCT ? 'green' : 'warn'}>{pct}%</Chip>}
                </div>
              );
            })
          )}
        </Card>

        {/* 참고 이력 (CBR) — 표시 전용 */}
        <Card style={{ ...CARD, flex: '3 1 560px', minWidth: 0, overflow: 'hidden' }} styles={{ body: { padding: 0 } }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '16px 20px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: colors.text }}>참고 이력 (CBR)</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 24, padding: '0 9px', borderRadius: radius.badge,
              background: colors.borderSubtle, color: colors.teal, fontSize: 12 }}>
              <InfoCircleOutlined /> 참고용 — 자동 적용되지 않음
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: GRID_CBR, gap: 12, padding: '10px 20px', background: colors.bgLayout,
            borderTop: `1px solid ${colors.borderSubtle}`, fontSize: 12, color: colors.textSecondary }}>
            <span>유사 업장</span><span>레시피</span><span style={{ textAlign: 'right' }}>회차</span><span>보정 재료</span>
            <span style={{ textAlign: 'right' }}>보정량</span><span style={{ textAlign: 'right' }}>일자</span><span style={{ textAlign: 'right' }}>유사도</span>
          </div>
          {detailLoading ? (
            <div style={{ display: 'grid', placeItems: 'center', minHeight: 120 }}><Spin /></div>
          ) : cbrRows.length === 0 ? (
            <div style={{ padding: '28px 20px', textAlign: 'center', fontSize: 13, color: colors.textTertiary, borderTop: `1px solid ${colors.borderSubtle}` }}>
              {recipeKey ? '재료 구성이 비슷한 다른 보정 이력이 아직 없어요' : '레시피를 고르면 비슷한 레시피의 보정 이력을 보여줘요'}
            </div>
          ) : (
            cbrRows.map((r) => (
              <div key={r.key} style={{ ...NUM, display: 'grid', gridTemplateColumns: GRID_CBR, gap: 12, alignItems: 'center',
                padding: '12px 20px', borderTop: `1px solid ${colors.borderSubtle}`, fontSize: 14 }}>
                <span style={{ color: colors.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.siteName}</span>
                <span style={{ color: colors.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.recipeName}</span>
                <span style={{ textAlign: 'right', color: colors.text }}>{r.rounds}회차</span>
                <span style={{ color: colors.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.ingredientName}</span>
                <span style={{ textAlign: 'right', color: colors.text }}>{signed(r.pct)}</span>
                <span style={{ textAlign: 'right', color: colors.textSecondary }}>{r.date}</span>
                <span style={{ textAlign: 'right', color: r.jaccard >= 0.8 ? colors.primaryActive : colors.textSecondary,
                  fontWeight: r.jaccard >= 0.8 ? 600 : 400 }}>{r.jaccard.toFixed(2)}</span>
              </div>
            ))
          )}
        </Card>
      </div>

      {registerModal}
    </div>
  );
}