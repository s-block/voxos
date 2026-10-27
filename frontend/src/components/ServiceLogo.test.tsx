import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BrandLogo } from "../api";
import { ServiceLogo } from "./ServiceLogo";

const openAiLogo: BrandLogo = {
  domain: "openai.com",
  label: "OpenAI",
  source: "domain",
};

describe("ServiceLogo", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("renders a Logo.dev image when a publishable key is available", () => {
    vi.stubEnv("VITE_LOGODEV_PUBLISHABLE_KEY", "pk_test");

    render(<ServiceLogo label="OpenAI" logo={openAiLogo} />);

    const logo = screen.getByRole("img", { name: "OpenAI logo" });
    const image = logo.querySelector("img");
    expect(image).not.toBeNull();
    expect(image?.getAttribute("src")).toContain("https://img.logo.dev/openai.com");
    expect(image?.getAttribute("src")).toContain("token=pk_test");
  });

  it("falls back to initials when the key is missing", () => {
    vi.stubEnv("VITE_LOGODEV_PUBLISHABLE_KEY", "");

    render(<ServiceLogo label="OpenAI" logo={openAiLogo} />);

    expect(screen.getByText("OP")).toBeInTheDocument();
  });

  it("falls back to initials after an image load error", () => {
    vi.stubEnv("VITE_LOGODEV_PUBLISHABLE_KEY", "pk_test");

    render(<ServiceLogo label="OpenAI" logo={openAiLogo} />);
    const image = screen.getByRole("img", { name: "OpenAI logo" }).querySelector("img");
    expect(image).not.toBeNull();

    fireEvent.error(image as HTMLImageElement);

    expect(screen.getByText("OP")).toBeInTheDocument();
  });
});
