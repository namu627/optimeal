// src/pages/Scaling.tsx
// 화면 8 · 레시피 스케일링
// 흐름: 레시피 선택(GET /api/scaling/recipes) → 1인분 미리보기(GET .../{key})
//       → 인원수·업장 입력 → 실행(POST /api/scaling/predict) → 결과표 + 신뢰도 배지 + 요약
// 주의(ADR-008): 업장 미선택이면 전 재료 cold-start 선형. 업장 보정이 쌓인 재료만 calibrated.
import { useEffect, useMemo, useState } from 'react';
import {
  Card, Select, InputNumber, Button, Table, Tag, Space, Typography, Empty,
  Tooltip, message, type TableProps,
} from 'antd';
import { ThunderboltOutlined, DownloadOutlined, SearchOutlined } from '@ant-design/icons';
import {
  listRecipes, getRecipe, predictScaling, listSites,
  type RecipeListItem, type RecipeBaseIngredient, type ScaledIngredient,
  type ScalingResponse, type SiteItem,
} from '../api/scaling';

const { Text } = Typography;

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7',
  line: '#EEF2F0', tableHead: '#F7FAF8',
  green: '#12A150', greenText: '#0B6B36', greenTint: '#E4F7EB',
};

const GROUP_TYPE_KR: Record<string, string> = {
  dry_heat: '건열', moist_heat: '습열', no_heat: '비가열',
};

function recipeLabel(r: RecipeListItem): string {
  const gt = GROUP_TYPE_KR[r.group_type] ?? r.group_type;
  const name = r.recipe_name?.trim();
  // 백엔드가 recipe_name 을 주면 "대구탕 · 끓이기(습열)", 아직 없으면 키로 폴백
  return name
    ? `${name} · ${r.cooking_method} (${gt})`
    : `${r.recipe_key} · ${r.cooking_method} (${gt})`;
}

/** method/confidence → 신뢰도 배지. 실제 응답 enum 기준 매핑. */
function ConfidenceTag({ row }: { row: ScaledIngredient }) {
  if (row.method === 'calibrated') {
    const color = row.confidence === 'high' ? 'green' : 'gold';
    return (
      <Tooltip title={`관측 ${row.n_obs}회 · confidence=${row.confidence}`}>
        <Tag color={color} style={{ marginInlineEnd: 0 }}>
          보정반영{row.n_obs ? ` · ${row.n_obs}회` : ''}
        </Tag>
      </Tooltip>
    );
  }
  return (
    <Tooltip title="이 업장 보정 이력이 없어 선형(base×N)으로 계산됨">
      <Tag style={{ marginInlineEnd: 0 }}>선형추정</Tag>
    </Tooltip>
  );
}

function downloadCsv(res: ScalingResponse) {
  const header = ['재료명', '역할', '1인분(g)', `${res.n_target}인분(g)`, '비율', 'method', 'n_obs', 'confidence'];
  const rows = res.ingredients.map((i) => [
    i.ingredient_name, i.role, i.base_amount_g, i.scaled_g,
    i.ratio, i.method, i.n_obs, i.confidence,
  ]);
  const csv = [header, ...rows]
    .map((r) => r.map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','))
    .join('\n');
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `scaling_${res.recipe_key}_${res.n_target}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Scaling() {
  const [recipes, setRecipes] = useState<RecipeListItem[]>([]);
  const [recipeLoading, setRecipeLoading] = useState(false);
  const [recipeKey, setRecipeKey] = useState<string | undefined>(undefined);
  const [preview, setPreview] = useState<RecipeBaseIngredient[] | null>(null);

  const [sites, setSites] = useState<SiteItem[]>([]);
  const [siteId, setSiteId] = useState<number | undefined>(undefined);
  const [nTarget, setNTarget] = useState<number>(320);

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ScalingResponse | null>(null);
  const [recents, setRecents] = useState<ScalingResponse[]>([]);

  useEffect(() => {
    setRecipeLoading(true);
    // 205개뿐이라 한 번에 전부 로드 → 이름 기반 검색/표시는 프론트에서 처리
    listRecipes('', 500).then(setRecipes).catch(() => undefined).finally(() => setRecipeLoading(false));
    listSites().then(setSites).catch(() => undefined);
  }, []);

  const nameByKey = useMemo(
    () => Object.fromEntries(recipes.map((r) => [r.recipe_key, r.recipe_name?.trim() || r.recipe_key])),
    [recipes],
  );

  const recipeOptions = useMemo(
    () =>
      recipes.map((r) => ({
        value: r.recipe_key,
        label: recipeLabel(r),
        // 메뉴명·키·조리법 어느 걸로 쳐도 걸리도록 검색 문자열 결합
        search: `${r.recipe_name ?? ''} ${r.recipe_key} ${r.cooking_method}`.toLowerCase(),
      })),
    [recipes],
  );

  const onRecipeChange = async (key: string) => {
    setRecipeKey(key);
    setPreview(null);
    try {
      const detail = await getRecipe(key);
      setPreview(Array.isArray(detail.ingredients) ? detail.ingredients : []);
    } catch {
      /* 인터셉터 토스트 */
    }
  };

  const onRun = async () => {
    if (!recipeKey) return message.warning('레시피를 선택하세요.');
    if (!nTarget || nTarget < 1) return message.warning('목표 인원수를 입력하세요.');
    setRunning(true);
    try {
      const res = await predictScaling({ recipe_key: recipeKey, n_target: nTarget, site_id: siteId ?? null });
      setResult(res);
      setRecents((prev) => [res, ...prev].slice(0, 6));
    } catch {
      /* 인터셉터 토스트 */
    } finally {
      setRunning(false);
    }
  };

  const columns: NonNullable<TableProps<ScaledIngredient>['columns']> = useMemo(
    () => [
      { title: '재료명', dataIndex: 'ingredient_name', key: 'name' },
      {
        title: '1인분(g)', dataIndex: 'base_amount_g', key: 'base', align: 'right', width: 104,
        render: (v: number) => <span style={{ color: C.sub }}>{v?.toLocaleString()}</span>,
      },
      {
        title: result ? `${result.n_target}인분(g)` : 'N인분(g)', dataIndex: 'scaled_g', key: 'scaled',
        align: 'right', width: 148,
        render: (v: number) => <b style={{ fontVariantNumeric: 'tabular-nums' }}>{Math.round(v).toLocaleString()}</b>,
      },
      {
        title: '비율', dataIndex: 'ratio', key: 'ratio', align: 'right', width: 88,
        render: (v: number) => <span style={{ color: C.sub }}>×{v?.toFixed(2)}</span>,
      },
      {
        title: '신뢰도', key: 'conf', align: 'right', width: 120,
        render: (_: unknown, row: ScaledIngredient) => <ConfidenceTag row={row} />,
      },
    ],
    [result],
  );

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 18, alignItems: 'start' }}>
      {/* ── 좌: 입력 + 최근 ── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
        <Card size="small" title="입력">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <Text style={{ fontSize: 12, color: C.sub }}>레시피</Text>
              <Select
                showSearch loading={recipeLoading}
                value={recipeKey} onChange={onRecipeChange}
                filterOption={(input, option) =>
                  ((option as { search?: string })?.search ?? '').includes(input.trim().toLowerCase())
                }
                placeholder="메뉴명 또는 레시피 키 검색"
                suffixIcon={<SearchOutlined />}
                style={{ width: '100%', marginTop: 6 }}
                options={recipeOptions}
                notFoundContent={recipeLoading ? '불러오는 중…' : '결과 없음'}
              />
            </div>

            <div>
              <Text style={{ fontSize: 12, color: C.sub }}>목표 인원수</Text>
              <InputNumber
                value={nTarget} min={1} max={5000} onChange={(v) => setNTarget(Number(v) || 0)}
                suffix="명" style={{ width: '100%', marginTop: 6 }}
              />
            </div>

            <div>
              <Text style={{ fontSize: 12, color: C.sub }}>업장</Text>
              <Select
                allowClear value={siteId} onChange={(v) => setSiteId(v)}
                placeholder="기본 (보정 없음)"
                style={{ width: '100%', marginTop: 6 }}
                options={sites.map((s) => ({ value: s.site_id, label: `${s.site_name} · ${s.site_type}` }))}
              />
              <Text style={{ fontSize: 12, color: C.muted, display: 'block', marginTop: 6 }}>
                업장을 선택하면 그 업장 보정값이 반영돼요
              </Text>
            </div>

            <Button
              type="primary" block icon={<ThunderboltOutlined />}
              loading={running} onClick={onRun}
            >
              스케일링 실행
            </Button>
          </div>
        </Card>

        <Card size="small" title="최근 스케일링">
          {recents.length === 0 ? (
            <Text style={{ fontSize: 13, color: C.muted }}>이번 세션에서 실행한 결과가 여기 쌓여요</Text>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {recents.map((r, i) => (
                <div
                  key={`${r.recipe_key}-${i}`}
                  onClick={() => setResult(r)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10, padding: '9px 4px',
                    borderTop: i ? `1px solid ${C.line}` : 'none', cursor: 'pointer',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, color: C.text }}>{nameByKey[r.recipe_key] ?? r.recipe_key}</div>
                    <div style={{ fontSize: 12, color: C.sub }}>
                      {r.n_target.toLocaleString()}인분 · {r.site_id ? `업장 ${r.site_id}` : '보정 없음'}
                    </div>
                  </div>
                  <Tag color={r.calibrated_count > 0 ? 'green' : 'default'} style={{ marginInlineEnd: 0 }}>
                    {r.calibrated_count > 0 ? `보정 ${r.calibrated_count}` : '선형'}
                  </Tag>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* ── 우: 결과 ── */}
      <Card
        size="small"
        title={result ? `스케일링 결과 · ${nameByKey[result.recipe_key] ?? result.recipe_key}` : '스케일링 결과'}
        extra={
          result ? (
            <Space size={12}>
              <Text style={{ fontSize: 12, color: C.sub }}>
                보정 {result.calibrated_count} · 선형 {result.cold_start_count}
              </Text>
              <Button size="small" icon={<DownloadOutlined />} onClick={() => downloadCsv(result)}>
                CSV
              </Button>
            </Space>
          ) : null
        }
        styles={{ body: { padding: result ? 0 : 24 } }}
      >
        {!result ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <div>
                <div style={{ color: C.text, fontWeight: 600 }}>레시피와 인원수를 입력하면 결과가 표시됩니다</div>
                <div style={{ color: C.sub, marginTop: 4, fontSize: 13 }}>
                  업장을 함께 고르면 보정값이 반영된 총량을 계산해요
                  {preview ? ` · 선택한 레시피 재료 ${preview.length}종` : ''}
                </div>
              </div>
            }
          />
        ) : (
          <>
            <Table<ScaledIngredient>
              rowKey="ingredient_id" columns={columns} dataSource={result.ingredients}
              pagination={false} size="middle"
            />
            <div style={{ padding: '12px 16px', color: C.muted, fontSize: 12, borderTop: `1px solid ${C.line}` }}>
              {result.disclaimer}
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
