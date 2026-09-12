export function LogoMark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" style={{ flex: 'none', display: 'block' }}>
      <circle cx="14" cy="15" r="2.5" fill="#12A150" />
      <circle cx="24" cy="11.5" r="3.5" fill="#12A150" />
      <circle cx="35" cy="14" r="5" fill="#12A150" />
      <path d="M5 26h38a19 19 0 0 1-38 0Z" fill="#12A150" />
    </svg>
  );
}