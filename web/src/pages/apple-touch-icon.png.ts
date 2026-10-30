import type { APIRoute } from "astro";
import sharp from "sharp";
import { voxosMarkSvg } from "../brand";

const SIZE = 180;
const MARK_SIZE = 136;

/** The brand mark centred on white, as iOS home-screen icons need a PNG. */
export const GET: APIRoute = async () => {
  const mark = await sharp(Buffer.from(voxosMarkSvg), { density: 400 }).resize(MARK_SIZE, MARK_SIZE).png().toBuffer();
  const icon = await sharp({
    create: { width: SIZE, height: SIZE, channels: 4, background: "#ffffff" },
  })
    .composite([{ input: mark, gravity: "center" }])
    .png()
    .toBuffer();
  return new Response(new Uint8Array(icon), { headers: { "Content-Type": "image/png" } });
};
