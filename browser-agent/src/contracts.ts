import { z } from "zod";

export const exactOriginSchema = z
  .string()
  .url()
  .max(2048)
  .refine((value) => {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.pathname === "/" &&
      !url.search &&
      !url.hash &&
      !url.username &&
      !url.password
    );
  }, "An exact HTTPS origin is required");

const startUrlSchema = z
  .string()
  .url()
  .max(4096)
  .refine((value) => {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.hash;
  }, "A public HTTPS start URL without credentials or a fragment is required");

const variableNameSchema = z.string().regex(/^[A-Za-z][A-Za-z0-9_]{0,63}$/);

const taskVariableSchema = z
  .object({
    value: z.string().min(1).max(8192),
    description: z.string().trim().min(1).max(512),
  })
  .strict();

const taskVariablesSchema = z
  .record(variableNameSchema, taskVariableSchema)
  .refine((variables) => Object.keys(variables).length <= 32, "At most 32 task variables are allowed");

export const browserTaskRequestSchema = z
  .object({
    instruction: z.string().trim().min(1).max(12_000),
    startUrl: startUrlSchema,
    allowedOrigins: z.array(exactOriginSchema).min(1).max(20),
    actionMode: z.enum(["read_only", "state_changes_allowed"]).default("read_only"),
    maxSteps: z.number().int().min(1).max(100),
    variables: taskVariablesSchema.default({}),
    model: z
      .object({
        adapter: z.enum(["stagehand_native", "openai_compatible"]),
        modelName: z.string().trim().min(1).max(512),
        baseUrl: z.string().url().max(2048),
        apiKey: z.string().min(1).max(8192),
      })
      .strict(),
  })
  .strict()
  .superRefine((value, context) => {
    const startOrigin = new URL(value.startUrl).origin;
    if (!value.allowedOrigins.includes(startOrigin)) {
      context.addIssue({
        code: "custom",
        message: "The start URL origin must be included in allowed origins",
        path: ["startUrl"],
      });
    }
    if (new Set(value.allowedOrigins).size !== value.allowedOrigins.length) {
      context.addIssue({
        code: "custom",
        message: "Allowed origins must be unique",
        path: ["allowedOrigins"],
      });
    }
  });

export const browserTaskOutputSchema = z.object({
  completed: z
    .boolean()
    .describe("Whether the browser objective was completed and visibly confirmed"),
  summary: z
    .string()
    .min(1)
    .max(12_000)
    .describe("The requested result, or concise evidence explaining why the objective was blocked"),
});

export type BrowserTaskRequest = z.infer<typeof browserTaskRequestSchema>;

export interface BrowserTaskResponse {
  status: "completed" | "blocked";
  output: string;
}
