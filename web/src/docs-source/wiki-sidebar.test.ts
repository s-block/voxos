import { describe, expect, it } from "vitest";
import { parseWikiSidebar } from "./wiki-sidebar";

describe("parseWikiSidebar", () => {
  it("returns top-level links, then one group per heading", () => {
    const groups = parseWikiSidebar(
      [
        "# Voxos Wiki",
        "",
        "Last updated: 2026-08-26",
        "",
        "- [Home](Home.md)",
        "- [Quick Start](Quick-Start.md)",
        "",
        "## Existing Focused Docs",
        "",
        "- [User MCP Servers](../user-mcp-servers.md)",
        "",
        "## Empty Group",
      ].join("\n"),
    );

    expect(groups).toEqual([
      {
        label: undefined,
        links: [
          { label: "Home", target: "Home.md" },
          { label: "Quick Start", target: "Quick-Start.md" },
        ],
      },
      {
        label: "Existing Focused Docs",
        links: [{ label: "User MCP Servers", target: "../user-mcp-servers.md" }],
      },
    ]);
  });

  it("rejects list items that are not a single link", () => {
    expect(() => parseWikiSidebar("# Wiki\n\n- Home\n")).toThrow(
      '_Sidebar.md:3: list items must be single Markdown links, found "- Home"',
    );
  });
});
