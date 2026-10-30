import type { APIRoute, GetStaticPaths } from "astro";
import { getCollection } from "astro:content";
import { renderOgCard, type OgCard } from "../../seo/og-card";
import { configuredSite } from "../../site-url";
import { site } from "../../site.config";

export const getStaticPaths = (async () => {
  const docs = await getCollection("docs");
  const landing: OgCard = {
    eyebrow: "Open source · MIT",
    title: "The open-source voice AI agent that picks up the phone",
    description: site.description,
  };
  return [
    { params: { id: "index" }, props: { card: landing } },
    ...docs.map((entry) => ({
      params: { id: entry.id },
      props: {
        card: {
          eyebrow: entry.id.startsWith("docs/skills/") ? "Example skill" : "Docs",
          title: entry.data.title,
          description: entry.data.description ?? site.description,
        } satisfies OgCard,
      },
    })),
  ];
}) satisfies GetStaticPaths;

export const GET: APIRoute<{ card: OgCard }> = async ({ props, site: siteUrl }) =>
  new Response(new Uint8Array(await renderOgCard(props.card, configuredSite(siteUrl))), {
    headers: { "Content-Type": "image/png" },
  });
