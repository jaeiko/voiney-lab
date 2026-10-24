import { product } from "../copy/ko";

// A placeholder mark (lane RX0, 2026-10-24): a sound wave running into a
// flask, beside the name. 임시 — 디자이너 확정 필요. Drawn inline so the
// screen has no image request and the mark follows the text colour.
export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role="img"
      aria-hidden="true"
      focusable="false"
      data-placeholder="designer-to-confirm"
    >
      <title>{product.logoNote}</title>
      <path
        d="M12 4h8v2h-1.5v7.2l6.1 10.6A3 3 0 0 1 22 28H10a3 3 0 0 1-2.6-4.2L13.5 13.2V6H12z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path
        d="M3 18h2l1.5-3 2 6 1.5-3h2"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M11 22h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="logo-lockup" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <LogoMark size={size} />
      <span style={{ fontWeight: 800, fontSize: size >= 36 ? "1.75rem" : "1.25rem", letterSpacing: "-0.01em" }}>
        {product.name}
      </span>
    </span>
  );
}
