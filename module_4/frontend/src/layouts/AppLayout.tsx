import { useState } from 'react';
import { Layout, Menu, Button, Avatar, Alert, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import {
  HomeOutlined,
  CalendarOutlined,
  UnorderedListOutlined,
  SearchOutlined,
  ColumnWidthOutlined,
  SlidersOutlined,
  SettingOutlined,
  PlusOutlined,
  DownOutlined,
} from '@ant-design/icons';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { colors, layout } from '../theme';
import { LogoMark } from '../components/Logo';
import { useHealth } from '../api/useHealth';

const items = [
  { key: '/', icon: <HomeOutlined />, label: '홈' },
  { key: '/plans/new', icon: <CalendarOutlined />, label: '식단 생성' },
  { key: '/plans', icon: <UnorderedListOutlined />, label: '식단 목록' },
  { type: 'divider' as const },
  { key: '/nutrition', icon: <SearchOutlined />, label: '영양성분 검색' },
  { key: '/scaling', icon: <ColumnWidthOutlined />, label: '레시피 스케일링' },
  { key: '/calibration', icon: <SlidersOutlined />, label: '캘리브레이션' },
  { key: '/settings', icon: <SettingOutlined />, label: '설정' },
];

const pageMeta: Record<string, { title: string; subtitle: string }> = {
  '/': { title: '홈', subtitle: '오늘 확인할 일과 진행 중인 식단' },
  '/plans': { title: '식단 목록', subtitle: '지난 식단을 찾아보고 복제할 수 있어요' },
  '/plans/new': { title: '식단 생성', subtitle: '조건을 입력하면 식단을 만들어 드려요' },
  '/nutrition': { title: '영양성분 검색', subtitle: '메뉴명으로 영양성분을 조회해요' },
  '/scaling': { title: '레시피 스케일링', subtitle: '1인분 레시피를 대량 조리량으로 변환해요' },
  '/calibration': { title: '캘리브레이션', subtitle: '현장 보정값을 기록해 정확도를 높여요' },
  '/settings': { title: '설정', subtitle: '계정과 기본값을 관리해요' },
};

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const ready = useHealth();

  const meta = pageMeta[pathname] ?? { title: '', subtitle: '' };

  const profileItems: MenuProps['items'] = [
    {
      key: 'me',
      label: (
        <div style={{ padding: '4px 0' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: colors.text }}>김영양</div>
          <div style={{ marginTop: 2, fontSize: 12, color: colors.textTertiary }}>서울초등학교 · 영양교사</div>
        </div>
      ),
      disabled: true,
    },
    { type: 'divider' },
    { key: 'info', label: '내 정보' },
    { key: 'store', label: '소속 업장 변경' },
    { key: 'settings', label: '설정' },
    { type: 'divider' },
    { key: 'logout', label: <span style={{ color: colors.errorText }}>로그아웃</span> },
  ];

  return (
    <Layout style={{ minHeight: '100vh', background: 'transparent' }}>
      <Layout.Sider
        width={layout.sidebarWidth}
        collapsedWidth={layout.sidebarCollapsedWidth}
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        style={{
          position: 'relative',
          background: 'rgba(255,255,255,0.72)',
          backdropFilter: 'blur(14px)',
          borderRight: `1px solid ${colors.border}`,
        }}
      >
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'flex-start',
            gap: 9,
            padding: collapsed ? 0 : '0 20px',
          }}
        >
          <LogoMark />
          {!collapsed && (
            <span style={{ fontWeight: 700, fontSize: 17, color: colors.text, letterSpacing: '-0.03em' }}>
              OptiMeal
            </span>
          )}
        </div>

        <Menu
          mode="inline"
          inlineCollapsed={collapsed}
          selectedKeys={[pathname]}
          items={items}
          onClick={(e) => navigate(e.key)}
          style={{ background: 'transparent', borderInlineEnd: 'none' }}
        />

        <div style={{ position: 'absolute', bottom: 52, left: 0, right: 0, padding: '0 14px' }}>
          {!collapsed && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '10px 6px',
                borderTop: `1px solid ${colors.borderSubtle}`,
              }}
            >
              <Avatar size={30} style={{ background: colors.primaryTintSoft, color: colors.primaryActive }}>
                영
              </Avatar>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: colors.text }}>김영양</div>
                <div style={{ fontSize: 11, color: colors.textTertiary }}>서울초등학교</div>
              </div>
            </div>
          )}

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: collapsed ? 'center' : 'flex-start',
              gap: 8,
              height: 28,
              padding: collapsed ? 0 : '0 8px',
              borderRadius: 8,
              background: ready === false ? colors.errorTint : colors.primaryTintSoft,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: ready === false ? colors.error : colors.primary,
                flex: 'none',
              }}
            />
            {!collapsed && (
              <span style={{ fontSize: 11, color: ready === false ? colors.errorText : colors.primaryActive }}>
                {ready === false ? '백엔드 준비중' : '백엔드 정상'}
              </span>
            )}
          </div>
        </div>
      </Layout.Sider>

      <Layout style={{ background: 'transparent' }}>
        <Layout.Header
          style={{
            height: layout.headerHeight,
            background: 'rgba(255,255,255,0.62)',
            backdropFilter: 'blur(14px)',
            borderBottom: `1px solid ${colors.borderTop}`,
            display: 'flex',
            alignItems: 'center',
            padding: '0 28px',
          }}
        >
          <div style={{ lineHeight: 1.3 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: colors.text }}>{meta.title}</div>
            <div style={{ fontSize: 12, color: colors.textTertiary }}>{meta.subtitle}</div>
          </div>

          <div style={{ flex: 1 }} />

          <Button type="primary" icon={<PlusOutlined />} disabled={ready === false} onClick={() => navigate('/plans/new')}>
            새 식단
          </Button>

          <div style={{ width: 1, height: 22, background: colors.borderSubtle, margin: '0 16px' }} />

          <Dropdown menu={{ items: profileItems }} trigger={['click']} placement="bottomRight">
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <Avatar size={30} style={{ background: colors.primaryTintSoft, color: colors.primaryActive }}>
                영
              </Avatar>
              <DownOutlined style={{ fontSize: 11, color: colors.textTertiary }} />
            </div>
          </Dropdown>
        </Layout.Header>

        {ready === false && (
          <Alert
            type="error"
            showIcon
            message="백엔드 준비 중입니다 — 마지막 저장 상태를 표시하고 있어요"
            style={{ borderRadius: 0, border: 'none' }}
          />
        )}

        <Layout.Content style={{ padding: layout.contentPadding }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}