import { describe, expect, it } from "vitest";
import { docPageNodes, faqNode, graph, serializeJsonLd, websiteNode } from "./structured-data";

describe("serializeJsonLd", () => {
  it("escapes angle brackets so content cannot end the script element", () => {
    const json = serializeJsonLd(
      graph(faqNode([{ question: "Does </script> break out?", answer: "No." }])),
    );

    expect(json).not.toContain("</script>");
    expect(JSON.parse(json)["@graph"][0].mainEntity[0].name).toBe("Does </script> break out?");
  });
});

describe("structured data URLs", () => {
  it("builds ids and breadcrumbs from the site the page belongs to", () => {
    const siteUrl = new URL("https://example.com");
    const nodes = docPageNodes({
      title: "Quick start",
      description: "Run Voxos locally.",
      url: new URL("/docs/quick-start/", siteUrl),
      image: new URL("/og/docs/quick-start.png", siteUrl),
      lastUpdated: undefined,
      section: { name: "Docs", url: new URL("/docs/", siteUrl) },
    });

    expect(websiteNode(siteUrl)).toMatchObject({ "@id": "https://example.com/#website", url: "https://example.com/" });
    expect(nodes).toMatchObject([
      { isPartOf: { "@id": "https://example.com/#website" }, about: { "@id": "https://example.com/#software" } },
      {
        itemListElement: [
          { item: "https://example.com/" },
          { item: "https://example.com/docs/" },
          { item: "https://example.com/docs/quick-start/" },
        ],
      },
    ]);
  });
});
