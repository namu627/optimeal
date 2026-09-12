import type { ThemeConfig } from 'antd';

export const colors = {
  primary: '#12A150',
  primaryHover: '#0E8A44',
  primaryActive: '#0B6B36',
  primaryTint: '#D7F0E0',
  primaryTintSoft: '#E4F7EB',

  text: '#16211C',
  textSecondary: '#5D6B64',
  textTertiary: '#8A9691',
  textDisabled: '#98A5A0',

  bgLayout: '#FBFDFC',
  bgContainer: '#FFFFFF',
  border: '#E7EFEA',
  borderTop: '#E9F0EC',
  borderSubtle: '#EEF2F0',
  hoverTint: 'rgba(236, 246, 241, 0.7)',

  success: '#12A150',
  warning: '#C8963E',
  warningText: '#8A6A1E',
  warningTint: '#FBF2DF',
  error: '#E4572E',
  errorText: '#A8380F',
  errorTint: '#FDEDE5',

  lime: '#4C7A17',
  mint: '#0B7360',
  teal: '#0F6E78',

  gaugeTrack: '#EDEAE3',
} as const;

export const pageBackground =
  'radial-gradient(700px 440px at 10% 6%, #E7F7EF 0%, rgba(231,247,239,0) 70%),' +
  'radial-gradient(580px 420px at 92% 18%, #E6F4F6 0%, rgba(230,244,246,0) 72%),' +
  'radial-gradient(720px 480px at 62% 104%, #F1F9E8 0%, rgba(241,249,232,0) 70%),' +
  '#FBFDFC';

export const layout = {
  sidebarWidth: 236,
  sidebarCollapsedWidth: 72,
  headerHeight: 56,
  contentPadding: '18px 28px',
  menuItemHeight: 34,
  menuItemRadius: 17,
} as const;

export const radius = {
  card: 16,
  glassCard: 20,
  button: 10,
  input: 10,
  dropdown: 14,
  badge: 8,
  table: 4,
} as const;

const theme: ThemeConfig = {
  token: {
    colorPrimary: colors.primary,
    colorSuccess: colors.success,
    colorWarning: colors.warning,
    colorError: colors.error,
    colorText: colors.text,
    colorTextSecondary: colors.textSecondary,
    colorTextTertiary: colors.textTertiary,
    colorTextDisabled: colors.textDisabled,
    colorBgLayout: colors.bgLayout,
    colorBgContainer: colors.bgContainer,
    colorBorder: colors.border,
    colorBorderSecondary: colors.borderSubtle,
    borderRadius: radius.button,
    borderRadiusLG: radius.card,
    fontFamily: 'Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    fontSize: 14,
    boxShadow: 'none',
    boxShadowSecondary: '0 12px 32px rgba(22, 33, 28, 0.12)',
  },
  components: {
    Layout: {
      headerBg: 'rgba(255, 255, 255, 0.62)',
      headerHeight: layout.headerHeight,
      siderBg: 'rgba(255, 255, 255, 0.72)',
      bodyBg: 'transparent',
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: colors.primaryTint,
      itemSelectedColor: colors.primaryActive,
      itemHoverBg: colors.hoverTint,
      itemColor: colors.textSecondary,
      itemHeight: layout.menuItemHeight,
      itemBorderRadius: layout.menuItemRadius,
      iconSize: 17,
    },
    Card: {
      borderRadiusLG: radius.card,
      colorBorderSecondary: colors.border,
      paddingLG: 20,
    },
    Table: {
      headerBg: '#FAFCFB',
      rowHoverBg: colors.hoverTint,
      borderColor: colors.borderSubtle,
      cellPaddingBlock: 12,
      headerColor: colors.textSecondary,
    },
    Button: {
      borderRadius: radius.button,
      controlHeight: 36,
      primaryShadow: 'none',
      defaultShadow: 'none',
      fontWeight: 600,
    },
    Input: { borderRadius: radius.input, controlHeight: 36 },
    Select: { borderRadius: radius.input, controlHeight: 36 },
    Tag: { borderRadiusSM: radius.badge, defaultBg: colors.primaryTintSoft },
    Alert: { borderRadiusLG: 12, withDescriptionPadding: '12px 16px' },
    Modal: { borderRadiusLG: radius.dropdown },
    Drawer: { paddingLG: 20 },
    Segmented: { borderRadius: radius.button, itemSelectedBg: '#FFFFFF' },
    Progress: { defaultColor: colors.primary },
  },
};

export default theme;