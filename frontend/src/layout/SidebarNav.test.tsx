import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { User } from "firebase/auth";
import { BrowserRouter, MemoryRouter, useLocation } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { SidebarNav } from "./SidebarNav";

describe("SidebarNav", () => {
  it("renders real links for every primary destination", () => {
    renderSidebar("/chat");

    expect(screen.getByRole("link", { name: "VoxOS" })).toHaveAttribute(
      "href",
      "/chat",
    );
    expect(screen.getByRole("link", { name: "Chat" })).toHaveAttribute(
      "href",
      "/chat",
    );
    expect(screen.getByRole("link", { name: "Voice" })).toHaveAttribute(
      "href",
      "/voice",
    );
    expect(screen.getByRole("link", { name: "Knowledge" })).toHaveAttribute(
      "href",
      "/knowledge",
    );
    expect(screen.getByRole("link", { name: "MCP" })).toHaveAttribute(
      "href",
      "/mcp",
    );
    expect(screen.getByRole("link", { name: "Contacts" })).toHaveAttribute(
      "href",
      "/contacts",
    );
    expect(
      screen.getByRole("link", { name: "Browser Accounts" }),
    ).toHaveAttribute("href", "/browser-accounts");
    expect(screen.getByRole("link", { name: /Test User.*Account/ })).toHaveAttribute(
      "href",
      "/account",
    );
  });

  it("keeps parent nav selected for nested routed views", () => {
    renderSidebar("/operations/history");

    expect(screen.getByRole("link", { name: "Operations" })).toHaveClass(
      "selected",
    );
    expect(screen.getByRole("link", { name: "Chat" })).not.toHaveClass(
      "selected",
    );
  });

  it("navigates by updating the router location", async () => {
    const user = userEvent.setup();
    renderSidebar("/chat");

    await user.click(screen.getByRole("link", { name: "Tools" }));

    expect(screen.getByLabelText("Current path")).toHaveTextContent("/tools");
  });

  it("supports browser back navigation after sidebar route changes", async () => {
    const user = userEvent.setup();
    window.history.pushState(null, "", "/chat");
    renderSidebarWithBrowserRouter();

    await user.click(screen.getByRole("link", { name: "Tools" }));
    expect(screen.getByLabelText("Current path")).toHaveTextContent("/tools");

    window.history.back();

    await waitFor(() => {
      expect(screen.getByLabelText("Current path")).toHaveTextContent("/chat");
    });
  });
});

function renderSidebar(initialPath: string) {
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <SidebarNav
        firebaseUser={testFirebaseUser}
        isCollapsed={false}
        onToggleCollapsed={vi.fn()}
      />
      <LocationProbe />
    </MemoryRouter>,
  );
}

function renderSidebarWithBrowserRouter() {
  render(
    <BrowserRouter>
      <SidebarNav
        firebaseUser={testFirebaseUser}
        isCollapsed={false}
        onToggleCollapsed={vi.fn()}
      />
      <LocationProbe />
    </BrowserRouter>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="Current path">{location.pathname}</output>;
}

const testFirebaseUser = {
  displayName: "Test User",
  email: "test@example.com",
  uid: "user-1",
} as User;
