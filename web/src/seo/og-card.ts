import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import satori from "satori";
import sharp from "sharp";
import { voxosMarkSvg } from "../brand";

export interface OgCard {
  readonly eyebrow: string;
  readonly title: string;
  readonly description: string;
}

type Style = Record<string, string | number>;

interface OgElement {
  readonly type: string;
  readonly props: {
    readonly style?: Style;
    readonly src?: string;
    readonly width?: number;
    readonly height?: number;
    readonly children?: OgNode | readonly OgNode[];
  };
}

type OgNode = OgElement | string;

const WIDTH = 1200;
const HEIGHT = 630;
/** Frame padding: the right and bottom leave room for the card's offset shadow. */
const PAD = { top: 32, right: 44, bottom: 44, left: 32 };
const require = createRequire(import.meta.url);
const markDataUri = `data:image/svg+xml;base64,${Buffer.from(voxosMarkSvg).toString("base64")}`;

let fontsPromise: ReturnType<typeof loadFonts> | undefined;

/** Satori reads WOFF, not WOFF2, so the cards use the WOFF files from the same Fontsource packages as the site. */
async function loadFonts() {
  const load = (file: string) => readFile(require.resolve(file));
  const [display, body] = await Promise.all([
    load("@fontsource/bricolage-grotesque/files/bricolage-grotesque-latin-800-normal.woff"),
    load("@fontsource/inter/files/inter-latin-500-normal.woff"),
  ]);
  return [
    { name: "Bricolage Grotesque", data: display, weight: 800 as const, style: "normal" as const },
    { name: "Inter", data: body, weight: 500 as const, style: "normal" as const },
  ];
}

const el = (type: string, style: Style, children?: OgElement["props"]["children"]): OgElement => ({
  type,
  props: { style, children },
});

function cardTree(card: OgCard, siteHost: string): OgElement {
  const titleSize = card.title.length > 48 ? 64 : card.title.length > 28 ? 76 : 88;
  return el(
    "div",
    {
      // Satori sizes boxes as content-box, so the frame's size excludes its padding.
      width: WIDTH - PAD.left - PAD.right,
      height: HEIGHT - PAD.top - PAD.bottom,
      display: "flex",
      padding: `${PAD.top}px ${PAD.right}px ${PAD.bottom}px ${PAD.left}px`,
      backgroundColor: "#ffffff",
      backgroundImage:
        "radial-gradient(circle at 8% 12%, rgba(25,240,196,0.35), transparent 38%), radial-gradient(circle at 95% 95%, rgba(102,87,255,0.28), transparent 42%)",
      fontFamily: "Inter",
      color: "#17211c",
    },
    el(
      "div",
      {
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        flexGrow: 1,
        padding: "44px 56px",
        border: "4px solid #17211c",
        borderRadius: 40,
        backgroundColor: "#ffffff",
        boxShadow: "12px 12px 0 #17211c",
      },
      [
        el("div", { display: "flex", alignItems: "center", justifyContent: "space-between" }, [
          el("div", { display: "flex", alignItems: "center", gap: 18 }, [
            { type: "img", props: { src: markDataUri, width: 72, height: 72 } },
            el("div", { display: "flex", fontFamily: "Bricolage Grotesque", fontSize: 48, letterSpacing: -2 }, [
              "Vox",
              el(
                "span",
                {
                  backgroundImage: "linear-gradient(110deg, #18e9d0, #5d63ff)",
                  backgroundClip: "text",
                  color: "transparent",
                },
                "OS",
              ),
            ]),
          ]),
          el(
            "div",
            {
              display: "flex",
              padding: "10px 24px",
              border: "3px solid #17211c",
              borderRadius: 999,
              backgroundColor: "#fff3c4",
              fontSize: 26,
              transform: "rotate(-3deg)",
            },
            card.eyebrow,
          ),
        ]),
        el("div", { display: "flex", flexDirection: "column", gap: 20 }, [
          el(
            "div",
            {
              display: "flex",
              fontFamily: "Bricolage Grotesque",
              fontSize: titleSize,
              lineHeight: 1.02,
              letterSpacing: -3,
            },
            card.title,
          ),
          el("div", { display: "flex", fontSize: 30, lineHeight: 1.35, color: "#3d4a44" }, truncate(card.description, 130)),
        ]),
        el("div", { display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: 26 }, [
          el("div", { display: "flex", color: "#66736d" }, siteHost),
          el("div", {
            display: "flex",
            width: 360,
            height: 14,
            borderRadius: 999,
            backgroundImage: "linear-gradient(90deg, #19f0c4, #16cfea 46%, #6657ff)",
          }),
        ]),
      ],
    ),
  );
}

function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return `${text.slice(0, text.lastIndexOf(" ", maxLength - 1)).replace(/[\s,.;:]+$/, "")}…`;
}

/** Renders a 1200×630 social card as PNG, with the site's host in its footer. */
export async function renderOgCard(card: OgCard, siteUrl: URL): Promise<Buffer> {
  fontsPromise ??= loadFonts();
  const svg = await satori(cardTree(card, siteUrl.host), { width: WIDTH, height: HEIGHT, fonts: await fontsPromise });
  return sharp(Buffer.from(svg)).png().toBuffer();
}
