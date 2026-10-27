import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createContact,
  deleteContact,
  loadContacts,
  updateContact,
  type Contact,
} from "../api";
import { ContactsPage } from "./ContactsPage";

vi.mock("../api", () => ({
  createContact: vi.fn(),
  deleteContact: vi.fn(),
  loadContacts: vi.fn(),
  updateContact: vi.fn(),
}));

describe("ContactsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(loadContacts).mockResolvedValue([]);
  });

  it("loads contacts and explains the caller access boundary", async () => {
    vi.mocked(loadContacts).mockResolvedValue([adaContact]);

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(screen.getByText("+442079460123")).toBeInTheDocument();
    expect(screen.getAllByText("Admin caller")).toHaveLength(2);
    expect(
      screen.getByText(/Caller ID can be spoofed, forwarded, or reassigned/),
    ).toBeInTheDocument();
  });

  it("creates a contact with all supported fields", async () => {
    const user = userEvent.setup();
    const onLog = vi.fn();
    vi.mocked(createContact).mockResolvedValue(adaContact);
    renderPage(onLog);

    await screen.findByText("No contacts yet.");
    await user.type(screen.getByLabelText("Display name"), "Ada Lovelace");
    await user.type(screen.getByLabelText("Phone number"), "+442079460123");
    await user.type(screen.getByLabelText("Email (optional)"), "ada@example.com");
    await user.type(screen.getByLabelText("Notes (optional)"), "Trusted caller");
    await user.click(screen.getByRole("switch", { name: /Admin caller/ }));
    await user.click(screen.getByRole("button", { name: "Create contact" }));

    expect(createContact).toHaveBeenCalledWith({
      display_name: "Ada Lovelace",
      email: "ada@example.com",
      is_admin: true,
      notes: "Trusted caller",
      phone_number: "+442079460123",
    });
    expect(
      await screen.findByRole("heading", { name: "Ada Lovelace" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Display name")).toHaveValue("");
    expect(onLog).toHaveBeenCalledWith("Contact created.");
  });

  it("edits a contact and clears optional fields", async () => {
    const user = userEvent.setup();
    const updatedContact: Contact = {
      ...adaContact,
      email: null,
      is_admin: false,
      notes: null,
      updated_at: "2026-07-13T11:00:00Z",
    };
    vi.mocked(loadContacts).mockResolvedValue([adaContact]);
    vi.mocked(updateContact).mockResolvedValue(updatedContact);
    renderPage();

    await user.click(
      await screen.findByRole("button", { name: "Edit Ada Lovelace" }),
    );
    expect(screen.getByRole("switch", { name: /Admin caller/ })).toBeChecked();

    await user.clear(screen.getByLabelText("Email (optional)"));
    await user.clear(screen.getByLabelText("Notes (optional)"));
    await user.click(screen.getByRole("switch", { name: /Admin caller/ }));
    await user.click(screen.getByRole("button", { name: "Save contact" }));

    expect(updateContact).toHaveBeenCalledWith("contact-1", {
      display_name: "Ada Lovelace",
      email: null,
      is_admin: false,
      notes: null,
      phone_number: "+442079460123",
    });
    expect(await screen.findByText("Limited caller")).toBeInTheDocument();
    expect(screen.queryByText("ada@example.com")).not.toBeInTheDocument();
    expect(screen.queryByText("Trusted caller")).not.toBeInTheDocument();
  });

  it("deletes a confirmed contact", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(loadContacts).mockResolvedValue([adaContact]);
    vi.mocked(deleteContact).mockResolvedValue();
    renderPage();

    await user.click(
      await screen.findByRole("button", { name: "Delete Ada Lovelace" }),
    );

    expect(confirm).toHaveBeenCalledWith("Delete Ada Lovelace?");
    expect(deleteContact).toHaveBeenCalledWith("contact-1");
    await waitFor(() => {
      expect(
        screen.queryByRole("heading", { name: "Ada Lovelace" }),
      ).not.toBeInTheDocument();
    });
    confirm.mockRestore();
  });

  it("shows API errors without discarding the draft", async () => {
    const user = userEvent.setup();
    const onLog = vi.fn();
    vi.mocked(createContact).mockRejectedValue(
      new Error("A contact already uses this phone number."),
    );
    renderPage(onLog);

    await screen.findByText("No contacts yet.");
    await user.type(screen.getByLabelText("Display name"), "Ada Lovelace");
    await user.type(screen.getByLabelText("Phone number"), "+442079460123");
    await user.click(screen.getByRole("button", { name: "Create contact" }));

    expect(
      await screen.findByText("A contact already uses this phone number."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Display name")).toHaveValue("Ada Lovelace");
    expect(onLog).toHaveBeenCalledWith(
      "Contact save failed: A contact already uses this phone number.",
      "error",
    );
  });
});

function renderPage(onLog = vi.fn()) {
  return render(<ContactsPage canEdit={true} onLog={onLog} />);
}

const adaContact: Contact = {
  created_at: "2026-07-13T10:00:00Z",
  display_name: "Ada Lovelace",
  email: "ada@example.com",
  id: "contact-1",
  is_admin: true,
  notes: "Trusted caller",
  phone_number: "+442079460123",
  updated_at: "2026-07-13T10:00:00Z",
  user_id: "user-1",
};
