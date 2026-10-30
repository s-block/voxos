import { describe, expect, it } from "vitest";
import { cookieDomains } from "./analytics";

describe("cookieDomains", () => {
  it("lists the host and its parent domains", () => {
    expect(cookieDomains("docs.example.com")).toEqual(["docs.example.com", "example.com"]);
    expect(cookieDomains("example.com")).toEqual(["example.com"]);
  });

  it("keeps hosts without parent domains as they are", () => {
    expect(cookieDomains("localhost")).toEqual(["localhost"]);
    expect(cookieDomains("127.0.0.1")).toEqual(["127.0.0.1"]);
  });
});
