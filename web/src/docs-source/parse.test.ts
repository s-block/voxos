import { describe, expect, it } from "vitest";
import { parseRepoDoc, plainText } from "./parse";

const wikiPage = `# Quick Start

Last updated: 2026-10-26

This page explains local setup with [Docker Compose](Developer-Setup.md) and the \`make up\` command.

## Prerequisites

- Docker
`;

describe("parseRepoDoc", () => {
  it("takes the title, date and description, and strips them from the body", () => {
    const doc = parseRepoDoc(wikiPage, "docs/wiki/Quick-Start.md");

    expect(doc.title).toBe("Quick Start");
    expect(doc.lastUpdated?.toISOString()).toBe("2026-10-26T00:00:00.000Z");
    expect(doc.description).toBe(
      "This page explains local setup with Docker Compose and the make up command.",
    );
    expect(doc.body.startsWith("This page explains")).toBe(true);
    expect(doc.body).not.toContain("# Quick Start");
    expect(doc.body).not.toContain("Last updated");
  });

  it("leaves pages without a Last updated line undated", () => {
    const doc = parseRepoDoc(
      "# Security policy\n\nSecurity fixes target the current main branch and its release images.\n",
      "SECURITY.md",
    );

    expect(doc.lastUpdated).toBeUndefined();
  });

  it("skips short status lines, lists, tables and code when choosing the description", () => {
    const doc = parseRepoDoc(
      [
        "# Catalog",
        "",
        "Last researched: 2026-07-28.",
        "",
        "```bash",
        "echo this code block is long enough to be mistaken for prose",
        "```",
        "",
        "| Need | Option |",
        "| --- | --- |",
        "",
        "- a list item that is also long enough to count as prose text",
        "",
        "This catalog complements the general guide and covers service specific setup.",
      ].join("\n"),
      "docs/mcp-service-catalog.md",
    );

    expect(doc.description).toBe(
      "This catalog complements the general guide and covers service specific setup.",
    );
  });

  it("truncates long descriptions on a word boundary", () => {
    const sentence = "Voxos combines voice, phone calls, SMS and WhatsApp in one agent backend. ";
    const doc = parseRepoDoc(`# Long\n\n${sentence.repeat(4)}\n`, "docs/wiki/Long.md");

    expect(doc.description.length).toBeLessThanOrEqual(160);
    expect(doc.description.endsWith("…")).toBe(true);
    expect(doc.description).not.toMatch(/\s…$/);
  });

  it("requires a title heading on the first line", () => {
    expect(() => parseRepoDoc("Intro without a title.\n", "docs/wiki/Bad.md")).toThrow(
      'docs/wiki/Bad.md: the first line must be a "# Title" heading',
    );
  });

  it("requires an intro paragraph for the description", () => {
    expect(() => parseRepoDoc("# Empty\n\n## Section\n\n- item\n", "docs/wiki/Empty.md")).toThrow(
      "docs/wiki/Empty.md: add an intro paragraph",
    );
  });
});

describe("plainText", () => {
  it("removes inline Markdown but keeps snake_case words", () => {
    expect(plainText("Use **`allowed_tools`** and [the *guide*](guide.md)!")).toBe(
      "Use allowed_tools and the guide!",
    );
  });
});
