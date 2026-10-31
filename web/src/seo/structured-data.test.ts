import { describe, expect, it } from "vitest";
import {
  type DocPageDetails,
  docPageNodes,
  faqNode,
  graph,
  organizationNode,
  serializeJsonLd,
  websiteNode,
} from "./structured-data";

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
  const siteUrl = new URL("https://example.com");
  const page: DocPageDetails = {
    title: "Quick start",
    description: "Run Voxos locally.",
    url: new URL("/docs/quick-start/", siteUrl),
    image: new URL("/og/docs/quick-start.png", siteUrl),
    lastUpdated: undefined,
    section: { name: "Docs", url: new URL("/docs/", siteUrl) },
  };

  it("builds ids and breadcrumbs from the site the page belongs to", () => {
    const nodes = docPageNodes(page);

    expect(websiteNode(siteUrl)).toMatchObject({ "@id": "https://example.com/#website", url: "https://example.com/" });
    expect(nodes[0]).not.toHaveProperty("dateModified");
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

  it("credits the project as author and publisher, and dates changes with a time zone", () => {
    const organization = { "@id": "https://example.com/#organization" };
    const [article] = docPageNodes({ ...page, lastUpdated: new Date("2026-10-26T00:00:00Z") });

    expect(article).toMatchObject({
      dateModified: "2026-10-26T00:00:00.000Z",
      author: organization,
      publisher: organization,
    });
    expect(websiteNode(siteUrl)).toMatchObject({ publisher: organization });
    expect(organizationNode(siteUrl)).toMatchObject({
      ...organization,
      url: "https://example.com/",
      logo: "https://example.com/apple-touch-icon.png",
    });
  });
});
