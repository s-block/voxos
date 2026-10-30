import { describe, expect, it } from "vitest";
import { resolveRepoLink, rewriteMarkdownLinks, type RepoLinkTargets } from "./links";

const targets: RepoLinkTargets = {
  routes: new Map([
    ["docs/wiki/Home.md", "/docs/"],
    ["docs/wiki/Quick-Start.md", "/docs/quick-start/"],
    ["docs/wiki/MCP-And-Tools.md", "/docs/mcp-and-tools/"],
    ["docs/user-mcp-servers.md", "/docs/user-mcp-servers/"],
  ]),
  blobUrl: (path) => `https://github.example/blob/${path}`,
  treeUrl: (path) => `https://github.example/tree/${path}`,
};

const fromWiki = (href: string): string => resolveRepoLink(href, "docs/wiki/Home.md", targets);

describe("resolveRepoLink", () => {
  it("maps published pages to site routes and keeps fragments", () => {
    expect(fromWiki("Quick-Start.md")).toBe("/docs/quick-start/");
    expect(fromWiki("Quick-Start.md#docker-compose-stack")).toBe("/docs/quick-start/#docker-compose-stack");
    expect(fromWiki("../user-mcp-servers.md")).toBe("/docs/user-mcp-servers/");
    expect(resolveRepoLink("wiki/MCP-And-Tools.md#google", "docs/mcp-service-catalog.md", targets)).toBe(
      "/docs/mcp-and-tools/#google",
    );
  });

  it("sends other repository files and folders to GitHub", () => {
    expect(fromWiki("../livekit-multi-tenant-worker-architecture.md")).toBe(
      "https://github.example/blob/docs/livekit-multi-tenant-worker-architecture.md",
    );
    expect(resolveRepoLink("examples/", "helm/README.md", targets)).toBe(
      "https://github.example/tree/helm/examples",
    );
    expect(resolveRepoLink("/Makefile?plain=1#L10", "app/README.md", targets)).toBe(
      "https://github.example/blob/Makefile?plain=1#L10",
    );
    expect(resolveRepoLink("../../", "docs/wiki/Home.md", targets)).toBe("https://github.example/tree/");
  });

  it("leaves external, mail, protocol-relative and in-page links alone", () => {
    for (const href of [
      "https://docs.livekit.io/",
      "mailto:security@example.com",
      "//cdn.example.com/a.js",
      "#endpoint-reachability",
      "",
    ]) {
      expect(fromWiki(href)).toBe(href);
    }
  });

  it("rejects links that leave the repository", () => {
    expect(() => resolveRepoLink("../../../monzo-mcp/README.md", "docs/wiki/Home.md", targets)).toThrow(
      'docs/wiki/Home.md: link "../../../monzo-mcp/README.md" points outside the repository',
    );
  });
});

describe("rewriteMarkdownLinks", () => {
  it("makes doc links absolute and leaves fenced code alone", () => {
    const markdown = [
      "See [Quick Start](Quick-Start.md#install) and [the Makefile](../../Makefile).",
      "```md",
      "[literal](Quick-Start.md)",
      "```",
      "Read [the guide](<../user-mcp-servers.md>) or [LiveKit](https://docs.livekit.io/).",
    ].join("\n");

    expect(rewriteMarkdownLinks(markdown, "docs/wiki/Home.md", targets, "https://site.example")).toBe(
      [
        "See [Quick Start](https://site.example/docs/quick-start/#install) and [the Makefile](https://github.example/blob/Makefile).",
        "```md",
        "[literal](Quick-Start.md)",
        "```",
        "Read [the guide](<https://site.example/docs/user-mcp-servers/>) or [LiveKit](https://docs.livekit.io/).",
      ].join("\n"),
    );
  });
});
