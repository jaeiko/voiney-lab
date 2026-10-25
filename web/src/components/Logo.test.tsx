import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { clearSpace, Logo } from "./Logo";

describe("Logo", () => {
  it("is the brand file from this server, named Voiney Lab, sized by height with the file's ratio", () => {
    render(<Logo height={32} />);
    const img = screen.getByRole("img", { name: "Voiney Lab" });
    expect(img).toHaveAttribute("src", expect.stringMatching(/\/brand\/voiney-lab_logo_ink\.svg$/));
    expect(img).toHaveAttribute("height", "32");
    expect(img).toHaveAttribute("width", "140");
    expect(img).toHaveStyle({ margin: "4px" });
  });

  it("keeps a quarter of the icon's height clear around the mark (the file itself holds 8 of the 88 units)", () => {
    // 32 px: icon 26.2 px, 6.5 px needed, 2.9 px in the file -> 4 px more; 48 px: 39.3 / 9.8 / 4.4 -> 6 px.
    expect(clearSpace(32)).toBe(4);
    expect(clearSpace(48)).toBe(6);
    expect(clearSpace(88)).toBe(10);
    expect(clearSpace(16)).toBeGreaterThanOrEqual(2);
  });
});
