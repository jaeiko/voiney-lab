import { product } from "../copy/ko";

// The product logo (person's decision 2026-10-25): the file from the brand
// bundle, copied to web/public/brand/ and served by this server at
// /app/brand/ -- never drawn here, never recoloured. The bundle's rules: clear
// space of at least a quarter of the icon's height all round, the icon never
// under 16 px, no stretching, tilting, recolouring or shadow. The horizontal
// file is 384 x 88 units with the 72-unit icon and 8 units of clear space
// built in; the rest of the clear space is this element's margin.
export const BRAND_URL = `${import.meta.env.BASE_URL}brand/`;

const FILE_WIDTH = 384;
const FILE_HEIGHT = 88;
const ICON_HEIGHT = 72;
const BUILT_IN_CLEAR = 8;

export function clearSpace(height: number): number {
  const icon = (height * ICON_HEIGHT) / FILE_HEIGHT;
  const builtIn = (height * BUILT_IN_CLEAR) / FILE_HEIGHT;
  return Math.max(0, Math.ceil(icon / 4 - builtIn));
}

export function Logo({ height = 32 }: { height?: number }) {
  return (
    <img
      className="logo-mark"
      src={`${BRAND_URL}voiney-lab_logo_ink.svg`}
      alt={product.name}
      height={height}
      width={Math.round((height * FILE_WIDTH) / FILE_HEIGHT)}
      style={{ display: "block", margin: clearSpace(height) }}
      draggable={false}
    />
  );
}
