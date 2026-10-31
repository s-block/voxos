import { describe, expect, it } from "vitest";
import { titleHead } from "./loader";

describe("titleHead", () => {
  it("keeps a title that already names the project, in any case, from repeating it", () => {
    expect(titleHead("Voxos Helm chart")).toEqual([{ tag: "title", content: "Voxos Helm chart" }]);
    expect(titleHead("MCP Service Catalog For VoxOS")).toEqual([
      { tag: "title", content: "MCP Service Catalog For VoxOS" },
    ]);
  });

  it("leaves other titles to Starlight's site-name suffix", () => {
    expect(titleHead("Quick Start")).toEqual([]);
    expect(titleHead("Voxosphere")).toEqual([]);
  });
});
