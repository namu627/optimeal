// src/pages/NutritionSearch.tsx
// 화면 7 · 영양성분 검색
// 흐름: 메뉴명 검색(GET /api/nutrition/search) → 결과 표 → 행 클릭 시 상세 Drawer(GET /api/nutrition/{id})
// 상태: 초기 / 로딩 / 결과 0건 / 503(인프라 준비중) 분기
import { useState } from 'react';
import {
  Card, Input, Segmented, Table, Space, Typography, Empty, Drawer, Descriptions,
  Alert, Spin, type TableProps,
} from 'antd';
import {
  searchNutrition, getNutrition, isUnavailable,
  type NutritionRow, type NutritionDetail,
} from '../api/nutrition';

const { Text } = Typography;

const C = { text: '#16211C', sub: '#5D6B64', muted: '#98A5A0' };

const CATEGORIES = ['전체', '주식', '국', '찌개', '반찬', '후식'] as const;

const num = (v: number | null | undefined, unit = '') =>
  v == null ? '-' : `${v.toLocaleString()}${unit}`;

export default function NutritionSearch() {
  const [q, setQ] = useState('');
  const [category, setCategory] = useState<string>('전체');
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<NutritionRow[]>([]);
  const [count, setCount] = useState(0);
  const [searched, setSearched] = useState(false);
  const [unavailable, setUnavailable] = useState(false); // 503

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detail, setDetail] = useState<NutritionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const runSearch = async (keyword: string, cat: string) => {
    const term = keyword.trim();
    if (!term) return;
    setLoading(true);
    setUnavailable(false);
    try {
      const res = await searchNutrition({
        q: term,
        menu_category: cat === '전체' ? undefined : cat,
        limit: 50,
      });
      setRows(res.results ?? []);
      setCount(res.count ?? res.results?.length ?? 0);
      setSearched(true);
    } catch (err) {
      if (isUnavailable(err)) {
        setUnavailable(true);
      } else {
        setRows([]);
        setCount(0);
        setSearched(true);
      }
    } finally {
      setLoading(false);
    }
  };

  const onCategoryChange = (val: string) => {
    setCategory(val);
    if (searched && q.trim()) void runSearch(q, val); // 이미 검색했으면 즉시 재조회
  };

  const openDetail = async (id: number) => {
    setDrawerOpen(true);
    setDetail(null);
    setDetailLoading(true);
    try {
      setDetail(await getNutrition(id));
    } catch {
      /* 인터셉터 토스트 */
    } finally {
      setDetailLoading(false);
    }
  };

  const columns: NonNullable<TableProps<NutritionRow>['columns']> = [
    { title: '메뉴명', dataIndex: 'recipe_name', key: 'name' },
    {
      title: '카테고리', dataIndex: 'menu_category', key: 'cat', width: 96,
      render: (v: string | null) => <span style={{ color: C.sub }}>{v ?? '-'}</span>,
    },
    { title: '칼로리(kcal)', dataIndex: 'calories', key: 'kcal', align: 'right', width: 108, render: (v) => num(v) },
    { title: '단백질(g)', dataIndex: 'protein', key: 'protein', align: 'right', width: 92, render: (v) => num(v) },
    { title: '지방(g)', dataIndex: 'fat', key: 'fat', align: 'right', width: 84, render: (v) => num(v) },
    { title: '탄수화물(g)', dataIndex: 'carbs', key: 'carbs', align: 'right', width: 100, render: (v) => num(v) },
    { title: '나트륨(mg)', dataIndex: 'sodium', key: 'sodium', align: 'right', width: 100, render: (v) => num(v) },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Text style={{ fontSize: 13, color: C.sub }}>
        식약처·식품안전나라 데이터에서 메뉴별 영양성분을 찾아 레시피에 바로 연결해요.
      </Text>

      <Card size="small">
        <Space wrap size={12} align="center">
          <Input.Search
            allowClear
            placeholder="메뉴명 검색"
            style={{ width: 300 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onSearch={(v) => void runSearch(v, category)}
            enterButton
          />
          <Segmented
            options={CATEGORIES as unknown as string[]}
            value={category}
            onChange={(v) => onCategoryChange(v as string)}
          />
          <Text style={{ fontSize: 13, color: C.sub }}>
            {searched && !unavailable ? `결과 ${count.toLocaleString()}건` : ''}
          </Text>
        </Space>
      </Card>

      {/* 503 · 인프라 준비중 */}
      {unavailable ? (
        <Card size="small">
          <Alert
            type="warning" showIcon
            title="영양성분 DB가 아직 준비 중이에요"
            description="백엔드(모듈 1) 데이터가 적재되면 검색이 활성화됩니다. 스케일링 화면은 지금도 사용할 수 있어요."
          />
        </Card>
      ) : loading ? (
        <Card size="small"><div style={{ padding: 48, textAlign: 'center' }}><Spin /></div></Card>
      ) : !searched ? (
        <Card size="small">
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="메뉴명을 입력해 검색해 보세요" />
        </Card>
      ) : rows.length === 0 ? (
        <Card size="small">
          <Empty
            description={
              <div>
                <div style={{ color: C.text, fontWeight: 600 }}>‘{q.trim()}’ 검색 결과가 없어요</div>
                <div style={{ color: C.sub, marginTop: 4, fontSize: 13 }}>
                  띄어쓰기를 줄이거나 대표 메뉴명으로 다시 검색해 보세요
                </div>
              </div>
            }
          />
        </Card>
      ) : (
        <Card size="small" styles={{ body: { padding: 0 } }}>
          <Table<NutritionRow>
            rowKey="nutrition_id"
            columns={columns}
            dataSource={rows}
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            size="middle"
            onRow={(record) => ({
              onClick: () => void openDetail(record.nutrition_id),
              style: { cursor: 'pointer' },
            })}
          />
        </Card>
      )}

      <Drawer
        title={detail?.recipe_name ?? '영양성분 상세'}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        size={420}
      >
        {detailLoading ? (
          <div style={{ padding: 48, textAlign: 'center' }}><Spin /></div>
        ) : detail ? (
          <>
            <Text style={{ fontSize: 12, color: C.sub }}>
              {[detail.menu_category, detail.serving_size_g ? `1인분량 ${detail.serving_size_g}g` : null, detail.data_source]
                .filter(Boolean)
                .join(' · ')}
            </Text>
            <Descriptions column={1} size="small" style={{ marginTop: 16 }} styles={{ label: { color: C.sub, width: 120 } }}>
              <Descriptions.Item label="칼로리">{num(detail.calories, ' kcal')}</Descriptions.Item>
              <Descriptions.Item label="단백질">{num(detail.protein, ' g')}</Descriptions.Item>
              <Descriptions.Item label="지방">{num(detail.fat, ' g')}</Descriptions.Item>
              <Descriptions.Item label="포화지방">{num(detail.saturated_fat, ' g')}</Descriptions.Item>
              <Descriptions.Item label="탄수화물">{num(detail.carbs, ' g')}</Descriptions.Item>
              <Descriptions.Item label="당류">{num(detail.sugar, ' g')}</Descriptions.Item>
              <Descriptions.Item label="나트륨">{num(detail.sodium, ' mg')}</Descriptions.Item>
              <Descriptions.Item label="콜레스테롤">{num(detail.cholesterol, ' mg')}</Descriptions.Item>
            </Descriptions>
            <Alert
              style={{ marginTop: 12 }} type="info" showIcon
              title="출처값은 조리 시 간에 따라 변동될 수 있어요"
            />
          </>
        ) : (
          <Empty description="상세 정보를 불러오지 못했어요" />
        )}
      </Drawer>
    </div>
  );
}
