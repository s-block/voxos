import { ExternalLink, Globe2, MapPinned } from "lucide-react";
import { parser, RuleType, type MarkdownToJSX } from "markdown-to-jsx";
import Markdown from "markdown-to-jsx/react";
import { type ComponentPropsWithoutRef, useMemo } from "react";
import type { ChatPlaceCard } from "../api";

type MarkdownMessageProps = {
  isStreaming?: boolean;
  placeCards?: readonly ChatPlaceCard[];
  text: string;
};

type ChatSource = {
  href: string;
  hostname: string;
  title: string;
};

const maximumVisibleSources = 3;
const emptyPlaceCards: readonly ChatPlaceCard[] = [];

const markdownOptions = {
  disableParsingRawHTML: true,
  enforceAtxHeadings: true,
  forceBlock: true,
  forceWrapper: true,
  overrides: {
    a: SafeLink,
    img: ImagePlaceholder,
  },
} satisfies MarkdownToJSX.Options;

const streamingMarkdownOptions = {
  ...markdownOptions,
  optimizeForStreaming: true,
} satisfies MarkdownToJSX.Options;

export function MarkdownMessage({
  isStreaming = false,
  placeCards = emptyPlaceCards,
  text,
}: MarkdownMessageProps) {
  const safePlaceCards = useMemo(
    () => (isStreaming ? [] : validatedPlaceCards(placeCards)),
    [isStreaming, placeCards],
  );
  const placeCardHrefs = useMemo(
    () => new Set(safePlaceCards.flatMap((card) => [card.place_url, card.attribution_url])),
    [safePlaceCards],
  );
  const sources = useMemo(
    () => (isStreaming ? [] : extractChatSources(text, placeCardHrefs)),
    [isStreaming, placeCardHrefs, text],
  );

  return (
    <div className="chat-message-content chat-message-markdown">
      <Markdown
        className="chat-markdown-body"
        options={isStreaming ? streamingMarkdownOptions : markdownOptions}
      >
        {text}
      </Markdown>
      {isStreaming ? <span className="stream-cursor" aria-hidden="true" /> : null}
      {safePlaceCards.length > 0 ? (
        <PlaceCardList placeCards={safePlaceCards} />
      ) : null}
      {sources.length > 0 ? <SourceList sources={sources} /> : null}
    </div>
  );
}

function PlaceCardList({ placeCards }: { placeCards: ChatPlaceCard[] }) {
  return (
    <nav aria-label="Places" className="chat-place-list">
      <span className="chat-place-list-label">Places</span>
      {placeCards.map((card) => (
        <a
          aria-label={`Open ${card.title} in Google Maps (opens in a new tab)`}
          className="chat-place-card"
          href={card.place_url}
          key={`${card.provider}:${card.place_id}`}
          rel="noopener noreferrer"
          target="_blank"
        >
          <MapPinned aria-hidden="true" className="chat-place-card-icon" />
          <span className="chat-place-card-copy">
            <span className="chat-place-card-title">{card.title}</span>
            <span className="chat-place-card-attribution" translate="no">
              Google Maps
            </span>
          </span>
          <ExternalLink aria-hidden="true" className="chat-place-card-open-icon" />
        </a>
      ))}
    </nav>
  );
}

function SafeLink({ children, ...props }: ComponentPropsWithoutRef<"a">) {
  const { href, ...anchorProps } = props;
  if (safeWebUrl(href) === null) {
    return <span>{children}</span>;
  }

  return (
    <a
      {...anchorProps}
      href={href}
      rel="noopener noreferrer"
      target="_blank"
    >
      {children}
    </a>
  );
}

function ImagePlaceholder({ alt }: ComponentPropsWithoutRef<"img">) {
  const label = alt?.trim() || "Image";
  return (
    <span aria-label={label} className="chat-markdown-image" role="img">
      [{label}]
    </span>
  );
}

function SourceList({ sources }: { sources: ChatSource[] }) {
  return (
    <nav aria-label="Sources" className="chat-source-list">
      <span className="chat-source-list-label">Sources</span>
      {sources.map((source) => (
        <a
          className="chat-source-card"
          href={source.href}
          key={source.href}
          rel="noopener noreferrer"
          target="_blank"
        >
          <Globe2 aria-hidden="true" className="chat-source-card-site-icon" />
          <span className="chat-source-card-copy">
            <span className="chat-source-card-title">{source.title}</span>
            <span className="chat-source-card-host">{source.hostname}</span>
          </span>
          <ExternalLink
            aria-hidden="true"
            className="chat-source-card-open-icon"
          />
        </a>
      ))}
    </nav>
  );
}

function extractChatSources(
  text: string,
  excludedHrefs: ReadonlySet<string> = new Set(),
): ChatSource[] {
  const sources: ChatSource[] = [];
  const seenHrefs = new Set<string>();

  visitNodes(parser(text, markdownOptions), (node) => {
    if (node.type !== RuleType.link || node.target === null) {
      return;
    }

    const url = safeWebUrl(node.target);
    if (
      url === null ||
      excludedHrefs.has(node.target) ||
      excludedHrefs.has(url.href) ||
      seenHrefs.has(url.href)
    ) {
      return;
    }

    seenHrefs.add(url.href);
    const linkText = nodeText(node.children).replace(/\s+/g, " ").trim();
    sources.push({
      href: url.href,
      hostname: url.hostname,
      title:
        safeWebUrl(linkText)?.href === url.href
          ? url.hostname
          : linkText || url.hostname,
    });
  });

  return sources.slice(0, maximumVisibleSources);
}

function validatedPlaceCards(
  placeCards: readonly ChatPlaceCard[],
): ChatPlaceCard[] {
  const cards: ChatPlaceCard[] = [];
  const seenPlaceIds = new Set<string>();
  for (const card of placeCards) {
    const placeUrl = safeWebUrl(card.place_url);
    const attributionUrl = safeWebUrl(card.attribution_url);
    const title = card.title.trim();
    if (
      card.provider !== "google_maps" ||
      card.place_id.trim() === "" ||
      title === "" ||
      placeUrl === null ||
      attributionUrl === null ||
      !isGoogleMapsUrl(placeUrl) ||
      !isGoogleMapsUrl(attributionUrl) ||
      seenPlaceIds.has(card.place_id)
    ) {
      continue;
    }
    seenPlaceIds.add(card.place_id);
    cards.push({ ...card, title });
  }
  return cards;
}

function isGoogleMapsUrl(url: URL): boolean {
  const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
  const path = url.pathname || "/";
  const isMapsDestination =
    (hostname === "maps.app.goo.gl" && path !== "/") ||
    (hostname === "maps.google.com" &&
      (path === "/" || path === "/maps" || path.startsWith("/maps/"))) ||
    ((hostname === "google.com" || hostname === "www.google.com") &&
      (path === "/maps" || path.startsWith("/maps/")));
  const sensitiveQueryKeys = new Set([
    "access_token",
    "api_key",
    "apikey",
    "credential",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
  ]);
  return (
    url.protocol === "https:" &&
    (url.port === "" || url.port === "443") &&
    url.hash === "" &&
    isMapsDestination &&
    !Array.from(url.searchParams.keys()).some((key) =>
      sensitiveQueryKeys.has(key.toLowerCase()),
    )
  );
}

function safeWebUrl(value: string | undefined): URL | null {
  if (value === undefined) {
    return null;
  }

  try {
    const url = new URL(value);
    if (
      (url.protocol !== "http:" && url.protocol !== "https:") ||
      url.hostname === "" ||
      url.username !== "" ||
      url.password !== ""
    ) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

function visitNodes(
  nodes: MarkdownToJSX.ASTNode[],
  visitor: (node: MarkdownToJSX.ASTNode) => void,
) {
  for (const node of nodes) {
    visitor(node);
    switch (node.type) {
      case RuleType.blockQuote:
      case RuleType.heading:
      case RuleType.link:
      case RuleType.paragraph:
      case RuleType.textFormatted:
        visitNodes(node.children, visitor);
        break;
      case RuleType.orderedList:
      case RuleType.unorderedList:
        node.items.forEach((item) => visitNodes(item, visitor));
        break;
      case RuleType.table:
        node.header.forEach((cell) => visitNodes(cell, visitor));
        node.cells.forEach((row) =>
          row.forEach((cell) => visitNodes(cell, visitor)),
        );
        break;
      case RuleType.htmlBlock:
        if (node.children !== undefined) {
          visitNodes(node.children, visitor);
        }
        break;
      default:
        break;
    }
  }
}

function nodeText(nodes: MarkdownToJSX.ASTNode[]): string {
  return nodes
    .map((node) => {
      switch (node.type) {
        case RuleType.codeBlock:
        case RuleType.codeInline:
        case RuleType.frontmatter:
        case RuleType.htmlComment:
        case RuleType.text:
          return node.text;
        case RuleType.footnoteReference:
          return node.text;
        case RuleType.blockQuote:
        case RuleType.heading:
        case RuleType.link:
        case RuleType.paragraph:
        case RuleType.textFormatted:
          return nodeText(node.children);
        case RuleType.htmlBlock:
          return node.children === undefined ? "" : nodeText(node.children);
        default:
          return "";
      }
    })
    .join("");
}
