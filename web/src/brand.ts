/**
 * Brand artwork lives in `docs/design/`. The site imports it from there, so
 * the repo holds a single copy.
 */
import voxosMarkSvg from "../../docs/design/voxos-mark.svg?raw";

export { voxosMarkSvg };

/**
 * Inline SVG markup safe to repeat on one page: each copy gets its own id
 * prefix, and the artwork is hidden from assistive technology. Pages label
 * it through surrounding text.
 */
export function inlineSvg(svg: string, idPrefix: string): string {
  const ids = [...svg.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1] ?? "");
  let markup = svg
    .replace(/<title[^>]*>[\s\S]*?<\/title>/g, "")
    .replace(/<desc[^>]*>[\s\S]*?<\/desc>/g, "")
    // Only the root element loses its fixed size and labelling; shapes keep their geometry.
    .replace(/<svg\b[^>]*>/, (tag) =>
      tag
        .replace(/\s(role|aria-labelledby|width|height)="[^"]*"/g, "")
        .replace("<svg", '<svg aria-hidden="true" focusable="false"'),
    );
  for (const id of ids) {
    markup = markup
      .replaceAll(`id="${id}"`, `id="${idPrefix}-${id}"`)
      .replaceAll(`url(#${id})`, `url(#${idPrefix}-${id})`);
  }
  return markup;
}
