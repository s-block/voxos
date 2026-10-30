import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

interface ViewerRequest {
  uri: string;
  querystring: Record<string, { value: string; multiValue?: { value: string }[] }>;
}

type Handler = (event: { request: ViewerRequest }) => ViewerRequest | { statusCode: number; headers: { location: { value: string } } };

// CloudFront Functions are plain scripts, so load the deployed source as is.
const source = readFileSync(new URL("./cloudfront-function.js", import.meta.url), "utf8");
const handler = new Function(`${source}\nreturn handler;`)() as Handler;

const request = (uri: string, querystring: ViewerRequest["querystring"] = {}) =>
  handler({ request: { uri, querystring } });

describe("website CloudFront Function", () => {
  it("serves directory URLs from their index.html", () => {
    expect(request("/")).toMatchObject({ uri: "/index.html" });
    expect(request("/docs/quick-start/")).toMatchObject({ uri: "/docs/quick-start/index.html" });
  });

  it("passes file requests through", () => {
    expect(request("/docs/quick-start.md")).toMatchObject({ uri: "/docs/quick-start.md" });
    expect(request("/_astro/page.Ab12.js")).toMatchObject({ uri: "/_astro/page.Ab12.js" });
  });

  it("adds the trailing slash, keeping the query string", () => {
    expect(
      request("/docs", {
        tag: { value: "a", multiValue: [{ value: "a" }, { value: "b" }] },
        flag: { value: "" },
      }),
    ).toMatchObject({
      statusCode: 301,
      headers: { location: { value: "/docs/?tag=a&tag=b&flag" } },
    });
  });
});
