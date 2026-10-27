import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  Mail,
  Pencil,
  Phone,
  Plus,
  RefreshCw,
  Save,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import {
  createContact,
  deleteContact,
  loadContacts,
  updateContact,
  type Contact,
  type CreateContactRequest,
} from "../api";
import type { ContactFormValues, LogEntry } from "../appTypes";
import { authErrorMessage } from "../appUtils";
import { TextAreaField, TextField } from "../components/FormFields";

const E164_PHONE_PATTERN = /^\+[1-9]\d{1,14}$/;

const EMPTY_CONTACT_FORM: ContactFormValues = {
  displayName: "",
  email: "",
  isAdmin: false,
  notes: "",
  phoneNumber: "",
};

type ContactsPageProps = {
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

export function ContactsPage({ canEdit, onLog }: ContactsPageProps) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [form, setForm] = useState<ContactFormValues>(EMPTY_CONTACT_FORM);
  const [editingContactId, setEditingContactId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setContacts(sortContacts(await loadContacts()));
    } catch (loadError) {
      const message = authErrorMessage(loadError);
      setError(message);
      onLog(`Contact load failed: ${message}`, "error");
    } finally {
      setIsLoading(false);
    }
  }, [onLog]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refresh]);

  const resetForm = () => {
    setEditingContactId(null);
    setForm(EMPTY_CONTACT_FORM);
    setError(null);
  };

  const canSubmit =
    canEdit &&
    !isSaving &&
    form.displayName.trim() !== "" &&
    E164_PHONE_PATTERN.test(form.phoneNumber.trim());

  const saveContact = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    setIsSaving(true);
    setError(null);
    const request = contactRequest(form);
    try {
      const saved =
        editingContactId === null
          ? await createContact(request)
          : await updateContact(editingContactId, request);
      setContacts((current) => sortContacts(upsertContact(current, saved)));
      onLog(
        editingContactId === null ? "Contact created." : "Contact updated.",
      );
      resetForm();
    } catch (saveError) {
      const message = authErrorMessage(saveError);
      setError(message);
      onLog(`Contact save failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const editContact = (contact: Contact) => {
    setEditingContactId(contact.id);
    setForm({
      displayName: contact.display_name,
      email: contact.email ?? "",
      isAdmin: contact.is_admin,
      notes: contact.notes ?? "",
      phoneNumber: contact.phone_number,
    });
    setError(null);
  };

  const removeContact = async (contact: Contact) => {
    if (
      !canEdit ||
      isSaving ||
      !window.confirm(`Delete ${contact.display_name}?`)
    ) {
      return;
    }

    setIsSaving(true);
    setError(null);
    try {
      await deleteContact(contact.id);
      setContacts((current) =>
        current.filter((item) => item.id !== contact.id),
      );
      if (editingContactId === contact.id) {
        resetForm();
      }
      onLog("Contact deleted.");
    } catch (deleteError) {
      const message = authErrorMessage(deleteError);
      setError(message);
      onLog(`Contact delete failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const isEditing = editingContactId !== null;
  const phoneIsInvalid =
    form.phoneNumber.trim() !== "" &&
    !E164_PHONE_PATTERN.test(form.phoneNumber.trim());

  return (
    <div className="settings-page contacts-page">
      <div className="contacts-grid">
        <section className="settings-panel contact-editor">
          <div className="section-heading-row">
            <div className="section-title">
              <p className="eyebrow">Contacts</p>
              <h2>{isEditing ? "Edit contact" : "New contact"}</h2>
            </div>
            {isEditing ? (
              <button
                disabled={!canEdit || isSaving}
                onClick={resetForm}
                type="button"
              >
                <Plus aria-hidden="true" size={16} />
                New
              </button>
            ) : null}
          </div>

          <form className="settings-form" onSubmit={saveContact}>
            <TextField
              disabled={!canEdit || isSaving}
              label="Display name"
              onChange={(displayName) => setForm({ ...form, displayName })}
              placeholder="Ada Lovelace"
              value={form.displayName}
            />
            <TextField
              disabled={!canEdit || isSaving}
              label="Phone number"
              onChange={(phoneNumber) => setForm({ ...form, phoneNumber })}
              placeholder="+442079460123"
              type="tel"
              value={form.phoneNumber}
            />
            {phoneIsInvalid ? (
              <p className="error-text contact-field-error">
                Use E.164 format, including the leading + and country code.
              </p>
            ) : null}
            <TextField
              disabled={!canEdit || isSaving}
              label="Email (optional)"
              onChange={(email) => setForm({ ...form, email })}
              placeholder="ada@example.com"
              type="email"
              value={form.email}
            />
            <TextAreaField
              disabled={!canEdit || isSaving}
              label="Notes (optional)"
              onChange={(notes) => setForm({ ...form, notes })}
              placeholder="Useful context about this person."
              value={form.notes}
            />

            <label className="switch-row contact-admin-switch">
              <input
                checked={form.isAdmin}
                disabled={!canEdit || isSaving}
                onChange={(event) =>
                  setForm({ ...form, isAdmin: event.currentTarget.checked })
                }
                role="switch"
                type="checkbox"
              />
              <span className="switch-control" aria-hidden="true">
                <span />
              </span>
              <span className="switch-label">
                <strong>Admin caller</strong>
                <small>
                  Give this caller the agent's normally configured access.
                </small>
              </span>
            </label>
            <p className="contact-security-note">
              <ShieldAlert aria-hidden="true" size={17} />
              <span>
                Caller ID can be spoofed, forwarded, or reassigned. Only enable
                admin caller access for people you trust.
              </span>
            </p>

            <button className="primary" disabled={!canSubmit} type="submit">
              {isEditing ? (
                <Save aria-hidden="true" size={16} />
              ) : (
                <Plus aria-hidden="true" size={16} />
              )}
              {isEditing ? "Save contact" : "Create contact"}
            </button>
          </form>
        </section>

        <section className="settings-panel contact-list-panel">
          <div className="section-heading-row">
            <div className="section-title">
              <p className="eyebrow">People</p>
              <h2>Saved contacts ({contacts.length})</h2>
            </div>
            <button
              aria-busy={isLoading}
              disabled={isLoading || isSaving}
              onClick={() => void refresh()}
              type="button"
            >
              <RefreshCw aria-hidden="true" size={16} />
              Refresh
            </button>
          </div>

          {error !== null ? <p className="error-text">{error}</p> : null}
          {isLoading && contacts.length === 0 ? (
            <p className="muted">Loading contacts…</p>
          ) : null}
          {!isLoading && contacts.length === 0 ? (
            <p className="muted">No contacts yet.</p>
          ) : null}
          {contacts.length > 0 ? (
            <div className="contact-list">
              {contacts.map((contact) => (
                <article className="contact-card" key={contact.id}>
                  <div className="contact-card-header">
                    <div>
                      <h3>{contact.display_name}</h3>
                      <div className="contact-meta">
                        <a href={`tel:${contact.phone_number}`}>
                          <Phone aria-hidden="true" size={14} />
                          {contact.phone_number}
                        </a>
                        {contact.email !== null ? (
                          <a href={`mailto:${contact.email}`}>
                            <Mail aria-hidden="true" size={14} />
                            {contact.email}
                          </a>
                        ) : null}
                      </div>
                    </div>
                    <span
                      className={`access-chip ${contact.is_admin ? "granted" : ""}`}
                    >
                      {contact.is_admin ? "Admin caller" : "Limited caller"}
                    </span>
                  </div>
                  {contact.notes !== null ? (
                    <p className="contact-notes">{contact.notes}</p>
                  ) : null}
                  <div className="contact-actions">
                    <button
                      aria-label={`Edit ${contact.display_name}`}
                      disabled={!canEdit || isSaving}
                      onClick={() => editContact(contact)}
                      type="button"
                    >
                      <Pencil aria-hidden="true" size={15} />
                      Edit
                    </button>
                    <button
                      aria-label={`Delete ${contact.display_name}`}
                      className="danger"
                      disabled={!canEdit || isSaving}
                      onClick={() => void removeContact(contact)}
                      type="button"
                    >
                      <Trash2 aria-hidden="true" size={15} />
                      Delete
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}

function contactRequest(form: ContactFormValues): CreateContactRequest {
  const email = form.email.trim();
  const notes = form.notes.trim();
  return {
    display_name: form.displayName.trim(),
    email: email === "" ? null : email,
    is_admin: form.isAdmin,
    notes: notes === "" ? null : notes,
    phone_number: form.phoneNumber.trim(),
  };
}

function upsertContact(contacts: Contact[], contact: Contact): Contact[] {
  const existingIndex = contacts.findIndex((item) => item.id === contact.id);
  if (existingIndex === -1) {
    return [...contacts, contact];
  }
  return contacts.map((item) => (item.id === contact.id ? contact : item));
}

function sortContacts(contacts: Contact[]): Contact[] {
  return [...contacts].sort((left, right) =>
    left.display_name.localeCompare(right.display_name, undefined, {
      sensitivity: "base",
    }),
  );
}
