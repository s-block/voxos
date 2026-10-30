import { i18nLoader } from "@astrojs/starlight/loaders";
import { docsSchema, i18nSchema } from "@astrojs/starlight/schema";
import { defineCollection } from "astro:content";
import { z } from "astro/zod";
import { repoDocsLoader } from "./docs-source/loader";
import { buildDocsSite } from "./docs-source/sources";

export const collections = {
  docs: defineCollection({
    loader: repoDocsLoader(buildDocsSite()),
    schema: docsSchema({
      extend: z.object({
        /** Repo-relative path of the Markdown file the page is built from. */
        repoPath: z.string(),
      }),
    }),
  }),
  /** Starlight UI wording, in `src/content/i18n/en.json`. */
  i18n: defineCollection({ loader: i18nLoader(), schema: i18nSchema() }),
};
