import type { APIRoute, GetStaticPaths } from "astro";
import { WEB_INDEXNOW_KEY } from "astro:env/server";
import { indexNowKeyFromEnv } from "../seo/indexnow";

/** The IndexNow key file, `/<key>.txt`, built only when WEB_INDEXNOW_KEY is set. */
export const getStaticPaths = (() => {
  const key = indexNowKeyFromEnv(WEB_INDEXNOW_KEY);
  return key === undefined ? [] : [{ params: { indexNowKey: key }, props: { key } }];
}) satisfies GetStaticPaths;

// IndexNow checks that the file holds exactly the key.
export const GET: APIRoute<{ key: string }> = ({ props }) =>
  new Response(props.key, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
