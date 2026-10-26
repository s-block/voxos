import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { User } from "firebase/auth";
import { describe, expect, it, vi } from "vitest";
import { AccountSettingsPage } from "./AccountSettingsPage";
import { AuthGate } from "./AuthGate";

describe("server selection access", () => {
  it("is available from the signed-out screen", async () => {
    const onChangeServer = vi.fn();
    const user = userEvent.setup();
    render(
      <AuthGate
        authError={null}
        isAuthReady={true}
        isAuthWorking={false}
        onApple={() => undefined}
        onChangeServer={onChangeServer}
        onEmailSubmit={async () => undefined}
        onGoogle={() => undefined}
        serverUrl="https://one.example.com"
      />,
    );

    await user.click(screen.getByRole("button", { name: "Change" }));

    expect(onChangeServer).toHaveBeenCalledOnce();
  });

  it("is available from the signed-in account page", async () => {
    const changeServer = vi.fn();
    const user = userEvent.setup();
    const firebaseUser = {
      displayName: "VoxOS User",
      email: "user@example.com",
      uid: "firebase-user",
    } as User;
    render(
      <AccountSettingsPage
        changeServer={changeServer}
        firebaseUser={firebaseUser}
        isAuthWorking={false}
        serverUrl="https://one.example.com"
        signOutUser={async () => undefined}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Change server" }));

    expect(changeServer).toHaveBeenCalledOnce();
  });
});
