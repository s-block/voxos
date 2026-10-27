import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ServerSelector } from "./ServerSelector";

describe("ServerSelector", () => {
  it("selects a saved server", async () => {
    const onSelect = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <ServerSelector
        activeUrl="https://one.example.com"
        error={null}
        isWorking={false}
        onSelect={onSelect}
        servers={["https://one.example.com", "https://two.example.com"]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /two\.example\.com/i }));

    expect(onSelect).toHaveBeenCalledWith("https://two.example.com");
  });

  it("can add another server", async () => {
    const onSelect = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <ServerSelector
        activeUrl="https://one.example.com"
        error={null}
        isWorking={false}
        onSelect={onSelect}
        servers={["https://one.example.com"]}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Add server" }));
    await user.type(
      screen.getByRole("textbox", { name: "Server address" }),
      "https://two.example.com",
    );
    await user.click(screen.getByRole("button", { name: "Connect" }));

    expect(onSelect).toHaveBeenCalledWith("https://two.example.com");
  });
});
