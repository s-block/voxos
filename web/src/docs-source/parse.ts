/** Matches the metadata line that `voxos.product_docs.validation` requires on wiki pages. */
const LAST_UPDATED_PATTERN = /^Last updated:\s*(\d{4}-\d{2}-\d{2})\s*$/m;
const TITLE_PATTERN = /^#\s+(.+?)\s*#*\s*$/;

/** Shorter intro paragraphs (status or date lines) make poor search snippets. */
const MIN_DESCRIPTION_LENGTH = 50;
const MAX_DESCRIPTION_LENGTH = 160;
const NON_PROSE_BLOCK = /^(#|\||[-*+]\s|\d+\.\s|>|```|~~~|<|!\[)/;

export interface RepoDoc {
  readonly title: string;
  readonly description: string;
  readonly lastUpdated: Date | undefined;
  /** Markdown without the title heading and Last updated line, which the docs layout renders itself. */
  readonly body: string;
}

/** Splits a repository Markdown page into the metadata the site needs and its body. */
export function parseRepoDoc(markdown: string, repoPath: string): RepoDoc {
  const lines = markdown.replace(/^\uFEFF/, "").split(/\r?\n/);
  const titleIndex = lines.findIndex((line) => line.trim() !== "");
  const titleMatch = TITLE_PATTERN.exec(lines[titleIndex] ?? "");
  if (!titleMatch?.[1]) {
    throw new Error(`${repoPath}: the first line must be a "# Title" heading`);
  }
  const rest = lines.slice(titleIndex + 1).join("\n");
  const lastUpdatedMatch = LAST_UPDATED_PATTERN.exec(rest);
  const body = (
    lastUpdatedMatch ? rest.replace(lastUpdatedMatch[0], "") : rest
  ).replace(/^\s*\n/, "");

  return {
    title: plainText(titleMatch[1]),
    description: describe(body, repoPath),
    lastUpdated: lastUpdatedMatch?.[1]
      ? new Date(`${lastUpdatedMatch[1]}T00:00:00Z`)
      : undefined,
    body: body.trimStart(),
  };
}

function describe(body: string, repoPath: string): string {
  const paragraph = proseBlocks(body)
    .map(plainText)
    .find((text) => text.length >= MIN_DESCRIPTION_LENGTH);
  if (!paragraph) {
    throw new Error(
      `${repoPath}: add an intro paragraph of at least ${MIN_DESCRIPTION_LENGTH} characters for the page description`,
    );
  }
  return truncate(paragraph, MAX_DESCRIPTION_LENGTH);
}

/** Paragraphs outside fenced code, skipping headings, lists, tables, quotes and HTML. */
function proseBlocks(body: string): string[] {
  const blocks: string[] = [];
  let current: string[] = [];
  let fence: string | undefined;
  const flush = (): void => {
    const block = current.join(" ").trim();
    if (block && !NON_PROSE_BLOCK.test(block)) blocks.push(block);
    current = [];
  };
  for (const line of body.split("\n")) {
    const fenceMatch = /^\s*(```|~~~)/.exec(line);
    if (fence) {
      if (fenceMatch?.[1] === fence) fence = undefined;
      continue;
    }
    if (fenceMatch?.[1]) {
      flush();
      fence = fenceMatch[1];
      continue;
    }
    if (line.trim() === "") flush();
    else current.push(line.trim());
  }
  flush();
  return blocks;
}

/** Inline Markdown reduced to readable text for titles, descriptions and alt text. */
export function plainText(markdown: string): string {
  return markdown
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1$2")
    .replace(/\s+/g, " ")
    .trim();
}

function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  const cut = text.slice(0, maxLength - 1);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > maxLength / 2 ? cut.slice(0, lastSpace) : cut).replace(/[\s,.;:]+$/, "")}…`;
}
