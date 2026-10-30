import { githubUrls, repositoryUrl, site } from "../site.config";

type JsonLdNode = Record<string, unknown>;

export interface FaqEntry {
  readonly question: string;
  /** Plain-text answer, as shown on the page. */
  readonly answer: string;
}

const websiteId = (siteUrl: URL): string => new URL("/#website", siteUrl).href;
const softwareId = (siteUrl: URL): string => new URL("/#software", siteUrl).href;

/** Wraps nodes in one JSON-LD document. */
export function graph(...nodes: JsonLdNode[]): JsonLdNode {
  return { "@context": "https://schema.org", "@graph": nodes };
}

export function websiteNode(siteUrl: URL): JsonLdNode {
  return {
    "@type": "WebSite",
    "@id": websiteId(siteUrl),
    url: new URL("/", siteUrl).href,
    name: site.name,
    description: site.description,
    inLanguage: "en",
  };
}

/** Voxos itself, described both as an application and as its source code. */
export function softwareNode(siteUrl: URL): JsonLdNode {
  return {
    "@type": ["SoftwareApplication", "SoftwareSourceCode"],
    "@id": softwareId(siteUrl),
    name: site.name,
    description: site.description,
    url: new URL("/", siteUrl).href,
    applicationCategory: "CommunicationApplication",
    applicationSubCategory: "AI voice agent",
    operatingSystem: "Linux, macOS, Windows (Docker)",
    isAccessibleForFree: true,
    offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
    license: githubUrls.license,
    codeRepository: repositoryUrl,
    programmingLanguage: ["Python", "TypeScript", "Dart"],
    sameAs: [repositoryUrl],
  };
}

export function faqNode(entries: readonly FaqEntry[]): JsonLdNode {
  return {
    "@type": "FAQPage",
    mainEntity: entries.map((entry) => ({
      "@type": "Question",
      name: entry.question,
      acceptedAnswer: { "@type": "Answer", text: entry.answer },
    })),
  };
}

export interface DocPageDetails {
  readonly title: string;
  readonly description: string;
  readonly url: URL;
  readonly image: URL;
  readonly lastUpdated: Date | undefined;
  readonly isDocsHome: boolean;
}

export function docPageNodes(page: DocPageDetails): JsonLdNode[] {
  const home = new URL("/", page.url);
  const crumbs = [
    { name: site.name, item: home.href },
    ...(page.isDocsHome ? [] : [{ name: "Docs", item: new URL("/docs/", home).href }]),
    { name: page.title, item: page.url.href },
  ];
  return [
    {
      "@type": "TechArticle",
      headline: page.title,
      description: page.description,
      url: page.url.href,
      image: page.image.href,
      inLanguage: "en",
      ...(page.lastUpdated ? { dateModified: page.lastUpdated.toISOString().slice(0, 10) } : {}),
      isPartOf: { "@id": websiteId(home) },
      about: { "@id": softwareId(home) },
    },
    {
      "@type": "BreadcrumbList",
      itemListElement: crumbs.map((crumb, index) => ({
        "@type": "ListItem",
        position: index + 1,
        name: crumb.name,
        item: crumb.item,
      })),
    },
  ];
}

/** JSON for a `<script type="application/ld+json">`, escaped so content cannot close the tag. */
export function serializeJsonLd(data: JsonLdNode): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}
