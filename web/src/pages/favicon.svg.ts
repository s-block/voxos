import type { APIRoute } from "astro";
import { voxosMarkSvg } from "../brand";

export const GET: APIRoute = () =>
  new Response(voxosMarkSvg, { headers: { "Content-Type": "image/svg+xml" } });
