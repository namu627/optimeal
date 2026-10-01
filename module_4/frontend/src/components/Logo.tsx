// animated: 생성 중 화면(GeneratingOverlay)용 — 점 3개·그릇에 class 만 붙인다(모양·색 동일, 움직임은 그 화면의 CSS).
export function LogoMark({ size = 26, animated = false }: { size?: number; animated?: boolean }) {
  const cls = (name: string) => (animated ? name : undefined);
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" style={{ flex: 'none', display: 'block' }}>
      <circle className={cls('logo-steam logo-steam-1')} cx="14" cy="15" r="2.5" fill="#12A150" />
      <circle className={cls('logo-steam logo-steam-2')} cx="24" cy="11.5" r="3.5" fill="#12A150" />
      <circle className={cls('logo-steam logo-steam-3')} cx="35" cy="14" r="5" fill="#12A150" />
      <path className={cls('logo-bowl')} d="M5 26h38a19 19 0 0 1-38 0Z" fill="#12A150" />
    </svg>
  );
}
