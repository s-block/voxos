import { readFileSync } from "node:fs";
import { costCalculatorPage } from "../data/voice-costs";
import { parseRepoDoc } from "./parse";
import { repoFileUrl } from "./repo";
import type { DocsSite } from "./sources";

/**
 * The page index for `llms.txt`: every published doc with its title and
 * description, taken from the same Markdown as the pages.
 */
export function llmsTxtDetails(docs: DocsSite, siteUrl: URL): string {
  const pages = docs.sources.map((source) => {
    const doc = parseRepoDoc(readFileSync(repoFileUrl(source.repoPath), "utf8"), source.repoPath);
    const url = new URL(`/${source.id}/`, siteUrl);
    return `- [${doc.title}](${url}): ${source.description ?? doc.description}`;
  });
  return [
    "Voxos is MIT licensed and self-hosted: it runs on your own infrastructure with your own provider keys.",
    "## Docs",
    pages.join("\n"),
    "## Tools",
    `- [${costCalculatorPage.name}](${new URL(costCalculatorPage.path, siteUrl)}): ${costCalculatorPage.description}`,
  ].join("\n\n");
}
