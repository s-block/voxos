import { z } from "astro/zod";
import { parse as parseToml } from "smol-toml";

const catalogSchema = z.object({
  skills: z
    .array(
      z.object({
        name: z.string().min(1),
        slug: z.string().regex(/^[a-z\d-]+$/),
        description: z.string().min(1),
        body_path: z.string().endsWith(".md"),
      }),
    )
    .min(1),
});

export interface CatalogSkill {
  readonly name: string;
  readonly slug: string;
  readonly description: string;
  /** Path of the skill body, relative to the catalog file. */
  readonly bodyPath: string;
}

/** Reads `examples/skills/catalog.toml`, the source of each curated skill's name and description. */
export function parseSkillsCatalog(toml: string): CatalogSkill[] {
  return catalogSchema.parse(parseToml(toml)).skills.map((skill) => ({
    name: skill.name,
    slug: skill.slug,
    description: skill.description,
    bodyPath: skill.body_path,
  }));
}
