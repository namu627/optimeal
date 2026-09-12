import { useState } from 'react';
import { Layout, Menu, Button, Avatar } from 'antd';
import {
  HomeOutlined,
  CalendarOutlined,
  UnorderedListOutlined,
  SearchOutlined,
  ColumnWidthOutlined,
  SlidersOutlined,
  SettingOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { colors, layout } from '../theme';
import { LogoMark } from '../components/Logo';

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

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const { pathname } = useLocation();

  return (
    <Layout style={{ minHeight: '100vh', background: 'transparent' }}>
      <Layout.Sider
        width={layout.sidebarWidth}
        collapsedWidth={layout.sidebarCollapsedWidth}
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        style={{
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
          <div style={{ flex: 1 }} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/plans/new')}>
            새 식단
          </Button>
          <Avatar style={{ marginLeft: 16, background: colors.primaryTintSoft, color: colors.primaryActive }}>
            영
          </Avatar>
        </Layout.Header>

        <Layout.Content style={{ padding: layout.contentPadding }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}