import { readFile } from "node:fs/promises";
import { relative } from "node:path";
import { fileURLToPath } from "node:url";
import type { Loader, LoaderContext } from "astro/loaders";
import { githubUrls, site } from "../site.config";
import { parseRepoDoc } from "./parse";
import { repoFileUrl } from "./repo";
import type { DocSource, DocsSite } from "./sources";

/**
 * Loads the published repository Markdown into the Starlight `docs`
 * collection, reading the files where they live instead of copying them.
 */
export function repoDocsLoader(docs: DocsSite): Loader {
  return {
    name: "voxos-repo-docs",
    async load(context) {
      const ids = new Set(docs.sources.map((source) => source.id));
      for (const id of context.store.keys()) {
        if (!ids.has(id)) context.store.delete(id);
      }
      await Promise.all(docs.sources.map((source) => storeSource(source, context)));

      const { watcher, logger } = context;
      if (!watcher) return;
      const sourcesByPath = new Map(
        docs.sources.map((source) => [fileURLToPath(repoFileUrl(source.repoPath)), source]),
      );
      watcher.add([...sourcesByPath.keys()]);
      watcher.on("change", (changedPath) => {
        const source = sourcesByPath.get(changedPath);
        if (!source) return;
        storeSource(source, context).then(
          () => logger.info(`Reloaded ${source.repoPath}`),
          (error: unknown) => logger.error(`Could not reload ${source.repoPath}: ${String(error)}`),
        );
      });
    },
  };
}

/**
 * Starlight titles each page "<title> | Voxos". A page whose own title already
 * names the project, such as "Voxos Helm chart", keeps that title alone.
 */
export function titleHead(title: string): { tag: "title"; content: string }[] {
  const words = title.toLowerCase().split(/\W+/);
  return words.includes(site.name.toLowerCase()) ? [{ tag: "title", content: title }] : [];
}

/**
 * Entries carry no digest, so every load renders again. Rendering about 30
 * pages is quick, and it keeps pages current when the link plugin changes.
 */
async function storeSource(
  source: DocSource,
  { store, parseData, renderMarkdown, config }: LoaderContext,
): Promise<void> {
  const fileURL = repoFileUrl(source.repoPath);
  const doc = parseRepoDoc(await readFile(fileURL, "utf8"), source.repoPath);
  const data = await parseData({
    id: source.id,
    data: {
      title: doc.title,
      description: source.description ?? doc.description,
      lastUpdated: doc.lastUpdated,
      editUrl: githubUrls.edit(source.repoPath),
      repoPath: source.repoPath,
      head: titleHead(doc.title),
    },
  });
  store.set({
    id: source.id,
    data,
    body: doc.body,
    rendered: await renderMarkdown(doc.body, { fileURL }),
    filePath: relative(fileURLToPath(config.root), fileURLToPath(fileURL)),
  });
}
