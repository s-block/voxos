import { fileURLToPath } from "node:url";
import { satteri } from "@astrojs/markdown-satteri";
import starlight from "@astrojs/starlight";
import { defineConfig, fontProviders } from "astro/config";
import starlightLlmsTxt from "starlight-llms-txt";
import { repoLinksPlugin } from "./src/docs-source/link-plugin";
import { llmsTxtDetails } from "./src/docs-source/llms";
import { repoFileUrl } from "./src/docs-source/repo";
import { buildDocsSite } from "./src/docs-source/sources";
import { siteUrlFromEnv } from "./src/site-url";
import { githubUrls, repositoryUrl, site } from "./src/site.config";

const docs = buildDocsSite();
// The public URL is a deployment setting from web/.env or the environment.
const siteUrl = siteUrlFromEnv(process.env.WEB_SITE_URL);

/** A Fontsource family served from the installed package, so builds need no font CDN. */
function fontsourceFamily(
  name: string,
  cssVariable: `--${string}`,
  [firstWeight, ...otherWeights]: readonly [number, ...number[]],
  fallbacks: string[],
) {
  const slug = name.toLowerCase().replaceAll(" ", "-");
  const variant = (weight: number) => ({
    src: [`@fontsource/${slug}/files/${slug}-latin-${weight}-normal.woff2`] as [string],
    weight,
    style: "normal" as const,
  });
  type Variant = ReturnType<typeof variant>;
  const variants: [Variant, ...Variant[]] = [variant(firstWeight), ...otherWeights.map(variant)];
  return {
    name,
    cssVariable,
    provider: fontProviders.local(),
    fallbacks,
    options: { variants },
  };
}

export default defineConfig({
  site: siteUrl.origin,
  trailingSlash: "always",
  build: { format: "directory" },
  // Keep a space where prose wraps onto a new line around inline elements (Astro 7's "jsx" default drops it).
  compressHTML: true,
  markdown: {
    processor: satteri({ hastPlugins: [repoLinksPlugin(docs)] }),
  },
  fonts: [
    fontsourceFamily("Bricolage Grotesque", "--font-display", [800], ["sans-serif"]),
    fontsourceFamily("Inter", "--font-body", [400, 600, 700], ["sans-serif"]),
    fontsourceFamily("JetBrains Mono", "--font-mono", [400, 600], ["monospace"]),
    fontsourceFamily("Caveat", "--font-hand", [600], ["cursive"]),
  ],
  integrations: [
    {
      // Restart the dev server when the page list or grouping changes.
      name: "voxos-docs-navigation",
      hooks: {
        "astro:config:setup": ({ addWatchFile }) => {
          for (const file of docs.navigationFiles) addWatchFile(repoFileUrl(file));
        },
      },
    },
    starlight({
      title: site.name,
      description: site.description,
      favicon: "/favicon.svg",
      sidebar: docs.sidebar,
      customCss: ["./src/styles/starlight.css"],
      components: {
        Head: "./src/components/starlight/Head.astro",
        SiteTitle: "./src/components/starlight/SiteTitle.astro",
        SocialIcons: "./src/components/starlight/SocialIcons.astro",
        ThemeProvider: "./src/components/starlight/ThemeProvider.astro",
        ThemeSelect: "./src/components/starlight/ThemeSelect.astro",
      },
      markdown: {
        processedDirs: docs.sourceDirs.map((dir) => fileURLToPath(repoFileUrl(`${dir}/`))),
      },
      expressiveCode: {
        themes: ["github-light"],
        useStarlightDarkModeSwitch: false,
      },
      disable404Route: true,
      plugins: [
        starlightLlmsTxt({
          projectName: site.name,
          description: site.description,
          details: llmsTxtDetails(docs, siteUrl),
          optionalLinks: [
            { label: "Source code", url: repositoryUrl, description: "The Voxos GitHub repository" },
            { label: "License", url: githubUrls.license, description: "MIT License" },
          ],
        }),
      ],
    }),
  ],
});
