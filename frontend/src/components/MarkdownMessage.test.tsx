import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownMessage } from "./MarkdownMessage";

describe("MarkdownMessage", () => {
  it("renders the common structured markdown used in assistant replies", () => {
    render(
      <MarkdownMessage
        text={[
          "## Contact",
          "",
          "Josh's number is **+447787162634**.",
          "",
          "- Saved contact",
          "- `mobile` number",
        ].join("\n")}
      />,
    );

    expect(screen.getByRole("heading", { name: "Contact" })).toBeInTheDocument();
    expect(screen.getByText("+447787162634").tagName).toBe("STRONG");
    expect(screen.getByText("Saved contact").closest("li")).not.toBeNull();
    expect(screen.getByText("mobile").tagName).toBe("CODE");
  });

  it("opens safe links separately and does not parse raw HTML", () => {
    render(
      <MarkdownMessage text={'[Voxos](https://example.com)\n\n<strong>raw</strong>'} />,
    );

    const link = screen.getByRole("link", { name: "Voxos" });
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByText("raw", { selector: "strong" })).not.toBeInTheDocument();
  });

  it("does not make unsupported or credential-bearing URLs clickable", () => {
    render(
      <MarkdownMessage
        text={[
          "[Local file](file:///tmp/report.txt)",
          "[Email](mailto:person@example.com)",
          "[Misleading](https://example.com@attacker.example/path)",
        ].join("\n\n")}
      />,
    );

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Sources" })).not.toBeInTheDocument();
  });

  it("shows up to three deduplicated source cards after rendering completes", () => {
    render(
      <MarkdownMessage
        text={[
          "[OpenAI research](https://openai.com/research)",
          "[Duplicate](https://openai.com/research)",
          "https://example.com/news",
          "[MDN](https://developer.mozilla.org/docs)",
          "[Fourth source](https://fourth.example/article)",
        ].join("\n\n")}
      />,
    );

    const sources = screen.getByRole("navigation", { name: "Sources" });
    const sourceLinks = within(sources).getAllByRole("link");
    expect(sourceLinks).toHaveLength(3);
    expect(sourceLinks[0]).toHaveTextContent("OpenAI research");
    expect(sourceLinks[0]).toHaveTextContent("openai.com");
    expect(sourceLinks[1]).toHaveTextContent("example.com");
    expect(sourceLinks[2]).toHaveTextContent("MDN");
    for (const link of sourceLinks) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
  });

  it("renders provider-backed places separately with exact Google Maps links", () => {
    const placeUrl =
      "https://www.google.com/maps/place/?q=place_id:ChIJ123";
    render(
      <MarkdownMessage
        placeCards={[
          {
            attribution_url: placeUrl,
            directions_url:
              "https://www.google.com/maps/dir/?api=1&destination=ChIJ123",
            latitude: 42.4304,
            longitude: 19.2594,
            place_id: "ChIJ123",
            place_url: placeUrl,
            position: 0,
            provider: "google_maps",
            title: "Konoba Stari Grad",
          },
        ]}
        text={`Try [Konoba Stari Grad](${placeUrl}).`}
      />,
    );

    const places = screen.getByRole("navigation", { name: "Places" });
    const link = within(places).getByRole("link", {
      name: "Open Konoba Stari Grad in Google Maps (opens in a new tab)",
    });
    expect(link).toHaveAttribute("href", placeUrl);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveTextContent("Konoba Stari Grad");
    expect(link).toHaveTextContent("Google Maps");
    expect(
      screen.queryByRole("navigation", { name: "Sources" }),
    ).not.toBeInTheDocument();
  });

  it("does not render place cards with untrusted URLs", () => {
    render(
      <MarkdownMessage
        placeCards={[
          {
            attribution_url: "https://www.google.com/maps/place/unsafe",
            directions_url: null,
            latitude: null,
            longitude: null,
            place_id: "unsafe",
            place_url: "https://google.com.attacker.example/place",
            position: 0,
            provider: "google_maps",
            title: "Unsafe place",
          },
        ]}
        text="No trusted place links."
      />,
    );

    expect(
      screen.queryByRole("navigation", { name: "Places" }),
    ).not.toBeInTheDocument();
  });

  it.each([
    "https://accounts.google.com/signin",
    "https://www.google.com/url?q=https://attacker.example",
    "https://www.google.com/maps/place/example?key=secret-value",
    "https://www.google.com/maps/place/example#token=secret-value",
    "https://www.google.com:444/maps/place/example",
    "https://maps.app.goo.gl/",
  ])("does not render a non-Maps Google destination: %s", (placeUrl) => {
    render(
      <MarkdownMessage
        placeCards={[
          {
            attribution_url: "https://www.google.com/maps/place/example",
            directions_url: null,
            latitude: null,
            longitude: null,
            place_id: "unsafe",
            place_url: placeUrl,
            position: 0,
            provider: "google_maps",
            title: "Unsafe place",
          },
        ]}
        text="No trusted place links."
      />,
    );

    expect(
      screen.queryByRole("navigation", { name: "Places" }),
    ).not.toBeInTheDocument();
  });

  it("does not load remote images from model output", () => {
    render(<MarkdownMessage text="![Account diagram](https://example.com/pixel.png)" />);

    expect(screen.queryByRole("img", { name: "Account diagram" })).toHaveTextContent(
      "[Account diagram]",
    );
    expect(document.querySelector("img")).toBeNull();
  });

  it("hides incomplete formatting markers while a reply streams", () => {
    const { container } = render(
      <MarkdownMessage isStreaming text="The number is **+4477" />,
    );

    expect(container).not.toHaveTextContent("**");
    expect(container.querySelector(".stream-cursor")).not.toBeNull();
    expect(screen.queryByRole("navigation", { name: "Sources" })).not.toBeInTheDocument();
  });
});
