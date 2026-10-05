// src/pages/plan/RecipeDrawer.tsx
// 확정 식단의 메뉴별 레시피 화면 — 확정(Step3)·저장 상세(PlanDetail)에서 연다.
// 규칙: 데이터셋에 있는 것만 보여 준다. 재료·투입량은 recipe_ingredient_map, 조리 순서는
// 식품안전나라 원본(MANUAL01~20) 원문 그대로. 없는 칸은 빈 상태로 두고 문구를 만들어 채우지 않는다.
import { useEffect, useMemo, useState } from 'react';
import { Drawer, Empty, Grid, Segmented, Select, Spin, Table, Button } from 'antd';
import { colors } from '../../theme';
import { type MealPlan, type MenuRecipe, type RecipeIngredient } from '../../api/menu';
import {
  collectEntries, mainServingsOf, altServingsOf, recipeRequests, fetchRecipeRequests, lookupRecipe,
  amountBasis, perServing, BASIS_LINEAR,
  type RecipeEntry as Entry, type FetchedByServings as Fetched,
} from './recipeView';

export default function RecipeDrawer({ plan, open, onClose }: { plan: MealPlan; open: boolean; onClose: () => void }) {
  const screens = Grid.useBreakpoint();
  const wide = screens.md !== false;
  // 메뉴마다 조리 인원(entry.servings) — 일반식은 전체 인원에서 그 끼니 대체식 인원을 뺀 값, 대체식은 그룹 인원(CSV·PDF 와 같음).
  const tracks = useMemo(() => [
    { label: '일반식', entries: collectEntries(plan.weeks, mainServingsOf(plan)) },
    ...plan.alternatives.map((t) => ({ label: `대체식 · ${t.label}`, entries: collectEntries(t.weeks, altServingsOf(plan, t)) })),
  ], [plan]);
  const [trackIdx, setTrackIdx] = useState(0);
  const [selected, setSelected] = useState<string | undefined>(undefined);
  const [attempt, setAttempt] = useState(0);
  const track = tracks[trackIdx] ?? tracks[0];
  // 보고 있는 트랙만 조회한다 — 인원별로, 전체 인원은 응답에 없던 메뉴만(recipeRequests). reqKey 가 바뀌면 새 요청.
  const reqs = useMemo(() => recipeRequests(plan, track.entries), [plan, track]);
  const reqKey = reqs.length ? `${JSON.stringify(reqs)}#${attempt}` : '';
  const [result, setResult] = useState<{ key: string; data?: Fetched; failed?: boolean }>({ key: '' });

  useEffect(() => {
    if (!open || !reqKey) return;
    let alive = true;
    fetchRecipeRequests(reqs)
      .then((data) => { if (alive) setResult({ key: reqKey, data }); })
      .catch((e) => { console.error('[레시피] 조회 실패:', e); if (alive) setResult({ key: reqKey, failed: true }); });
    return () => { alive = false; };
  }, [open, reqKey, reqs]);

  const done = !reqKey || result.key === reqKey;
  const loading = !done;
  const failed = done && !!result.failed;
  // 다른 트랙의 조회 결과는 쓰지 않는다 — 인원이 다른 값이 남지 않게.
  const fetched: Fetched = (done && result.data) || new Map();

  const entries = track.entries;
  const current = entries.find((e) => e.key === selected) ?? entries[0];
  const recipeOf = (e: Entry) => lookupRecipe(plan, fetched, e);
  // 헤더 인원: 트랙 안 메뉴 인원이 하나면 그 값, 여럿이면 범위(일반식은 대체 자리 메뉴만 줄어든다).
  const svs = [...new Set(entries.map((e) => e.servings))].sort((a, b) => a - b);
  const servingsText = svs.length <= 1 ? `${(svs[0] ?? plan.headcount).toLocaleString()}명` : `${svs[0].toLocaleString()}~${svs[svs.length - 1].toLocaleString()}명`;

  const list = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {entries.map((e) => {
        const rec = recipeOf(e);
        const active = e.key === current?.key;
        const noSteps = rec && !rec.steps?.length;
        return (
          <button key={e.key} type="button" onClick={() => setSelected(e.key)}
            style={{
              textAlign: 'left', border: 'none', cursor: 'pointer', borderRadius: 10, padding: '9px 12px',
              background: active ? colors.primaryTintSoft : 'transparent',
            }}>
            <div style={{ fontSize: 13, fontWeight: active ? 700 : 500, color: active ? colors.primaryActive : colors.text, lineHeight: 1.4 }}>{e.name}</div>
            <div style={{ marginTop: 2, fontSize: 11, color: colors.textTertiary }}>
              {svs.length > 1 ? `${e.servings.toLocaleString()}명분 · ` : ''}{e.uses[0]}{e.uses.length > 1 ? ` 외 ${e.uses.length - 1}회` : ''}
              {rec && noSteps ? ' · 재료만' : ''}
            </div>
          </button>
        );
      })}
    </div>
  );

  return (
    <Drawer
      title={<div>
        <div style={{ fontSize: 16, fontWeight: 700, color: colors.text }}>레시피</div>
        <div style={{ fontSize: 12, fontWeight: 400, color: colors.textSecondary }}>{servingsText} 기준 총 투입량 · 메뉴 {entries.length}개</div>
      </div>}
      open={open} onClose={onClose} size={wide ? 920 : '100%'}
      // 본문 전체는 스크롤하지 않는다 — 메뉴 목록과 레시피 내용이 각자 따로 스크롤된다.
      styles={{ body: { padding: 0, background: colors.bgContainer, display: 'flex', flexDirection: 'column', overflow: 'hidden' } }}
    >
      <div style={{ flex: 'none', padding: '14px 20px', borderBottom: `1px solid ${colors.borderSubtle}`, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        {tracks.length > 1 && (
          <Segmented value={trackIdx} onChange={(v) => { setTrackIdx(Number(v)); setSelected(undefined); }}
            options={tracks.map((t, i) => ({ label: t.label, value: i }))} />
        )}
        {!wide && entries.length > 0 && (
          <Select style={{ flex: 1, minWidth: 200 }} value={current?.key} onChange={setSelected}
            options={entries.map((e) => ({ label: e.name, value: e.key }))} />
        )}
      </div>
      {!entries.length ? (
        <Empty style={{ padding: 48 }} description="이 식단에는 메뉴가 없어요" />
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'stretch' }}>
          {wide && (
            <div style={{ width: 250, flexShrink: 0, borderRight: `1px solid ${colors.borderSubtle}`, padding: 10, overflowY: 'auto' }}>{list}</div>
          )}
          {/* key: 다른 메뉴를 고르면 레시피 영역을 새로 그려 스크롤이 맨 위에서 시작한다 */}
          <div key={current?.key} style={{ flex: 1, minWidth: 0, padding: wide ? '20px 28px' : '16px', overflowY: 'auto' }}>
            {current && (
              <RecipeDetail entry={current} rec={recipeOf(current)} headcount={current.servings}
                loading={loading && !recipeOf(current)} failed={failed && !recipeOf(current)}
                onRetry={() => setAttempt((a) => a + 1)} />
            )}
          </div>
        </div>
      )}
    </Drawer>
  );
}

const fmt = (v: number) => (Number.isInteger(v) ? v : Math.round(v * 10) / 10).toLocaleString();

function RecipeDetail({ entry, rec, headcount, loading, failed, onRetry }: {
  entry: Entry; rec?: MenuRecipe; headcount: number; loading: boolean; failed: boolean; onRetry: () => void;
}) {
  const section = (title: string, extra?: string) => (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, margin: '24px 0 10px' }}>
      <span style={{ fontSize: 14, fontWeight: 700, color: colors.text }}>{title}</span>
      {extra && <span style={{ fontSize: 12, color: colors.textTertiary }}>{extra}</span>}
    </div>
  );
  const blank = (text: string) => (
    <div style={{ border: `1px dashed ${colors.border}`, borderRadius: 12, padding: '18px 16px', fontSize: 13, color: colors.textTertiary, textAlign: 'center' }}>{text}</div>
  );

  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 700, color: colors.text, lineHeight: 1.35 }}>{entry.name}</div>
      <div style={{ marginTop: 6, fontSize: 12, color: colors.textSecondary, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        {rec?.cooking_method && (
          <span style={{ color: colors.primaryActive, background: colors.primaryTintSoft, borderRadius: 6, padding: '1px 8px', fontWeight: 600 }}>{rec.cooking_method}</span>
        )}
        <span>{entry.uses.join(' · ')}</span>
      </div>

      {loading ? (
        <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div>
      ) : failed ? (
        <div style={{ marginTop: 24 }}>{blank('레시피를 불러오지 못했어요')}
          <div style={{ textAlign: 'center', marginTop: 10 }}><Button size="small" onClick={onRetry}>다시 시도</Button></div>
        </div>
      ) : (
        <>
          {section('재료', rec?.ingredients.length ? `${rec.ingredients.length}종 · ${headcount}명 총량` : undefined)}
          {rec?.ingredients.length ? (
            <Table<RecipeIngredient>
              size="small" pagination={false} rowKey={(r, i) => `${r.name}-${i}`}
              dataSource={rec.ingredients}
              columns={[
                { title: '재료명', dataIndex: 'name' },
                { title: '역할', dataIndex: 'role', width: 90, render: (v) => v ?? '' },
                {
                  title: '투입량 (g, 총량)', dataIndex: 'amount', width: 140, align: 'right',
                  render: (v: number | null | undefined) => v == null ? <span style={{ color: colors.textTertiary }}>분량 미기재</span> : fmt(v),
                },
                {
                  title: '1인분 (g)', key: 'per', width: 100, align: 'right',
                  render: (_, r) => { const per = perServing(r, headcount); return per == null ? '' : <span style={{ color: colors.textSecondary }}>{fmt(per)}</span>; },
                },
                {
                  // 총량 산출 기준 — 스케일링(업장 보정값) | 단순 비례(1인분×인원). CSV·PDF 도 같은 함수(amountBasis).
                  title: '기준', key: 'basis', width: 92,
                  render: (_, r) => {
                    const b = amountBasis(r);
                    if (!b) return '';
                    const scaled = b !== BASIS_LINEAR;
                    return <span style={{ fontSize: 12, borderRadius: 6, padding: '1px 7px', whiteSpace: 'nowrap',
                      color: scaled ? colors.primaryActive : colors.textSecondary, background: scaled ? colors.primaryTintSoft : colors.borderSubtle }}>{b}</span>;
                  },
                },
              ]}
              style={{ fontVariantNumeric: 'tabular-nums' }}
            />
          ) : blank('재료 데이터 없음')}

          {section('조리 순서', rec?.steps?.length ? `${rec.steps.length}단계 · 원문` : undefined)}
          {rec?.steps?.length ? (
            <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
              {rec.steps.map((s, i) => (
                <li key={i} style={{ fontSize: 14, color: colors.text, lineHeight: 1.65, whiteSpace: 'pre-wrap', paddingBottom: 10, borderBottom: i < rec.steps!.length - 1 ? `1px solid ${colors.borderSubtle}` : 'none' }}>{s}</li>
              ))}
            </ol>
          ) : blank('레시피 미등록 — 데이터셋에 조리 순서가 없어요')}
        </>
      )}
    </div>
  );
}