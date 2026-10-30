import { describe, expect, it } from "vitest";
import { inlineSvg, voxosMarkSvg } from "./brand";

describe("inlineSvg", () => {
  it("prefixes ids and their references so copies can share a page", () => {
    const markup = inlineSvg(voxosMarkSvg, "header");

    expect(markup).toContain('id="header-voxos-mark-flow"');
    expect(markup).toContain("url(#header-voxos-mark-flow)");
    expect(markup).not.toContain('id="voxos-mark-flow"');
  });

  it("hides the artwork from assistive technology and drops the fixed size", () => {
    const markup = inlineSvg(voxosMarkSvg, "footer");
    const root = /<svg\b[^>]*>/.exec(markup)?.[0] ?? "";

    expect(root).toMatch(/^<svg aria-hidden="true" focusable="false"/);
    expect(root).not.toMatch(/aria-labelledby|\swidth="|\sheight="/);
    expect(markup).not.toMatch(/<title|<desc/);
  });

  it("keeps the size of the shapes inside the artwork", () => {
    const markup = inlineSvg(voxosMarkSvg, "header");

    expect(markup).toContain('<rect x="39.5" y="54.5" width="6" height="29.5" rx="3"/>');
  });
});
