from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from voxos.utils.phone_numbers import normalize_e164_phone_number

DISPLAY_NAME_MAX_LENGTH = 256
EMAIL_MAX_LENGTH = 320
NOTES_MAX_LENGTH = 2048


class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH)
    phone_number: str = Field(min_length=2, max_length=16)
    email: EmailStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    notes: str | None = Field(default=None, min_length=1, max_length=NOTES_MAX_LENGTH)
    is_admin: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone_number(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return normalize_e164_phone_number(value)


class ContactCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str = Field(min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH)
    phone_number: str = Field(min_length=2, max_length=16)
    email: EmailStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    notes: str | None = Field(default=None, min_length=1, max_length=NOTES_MAX_LENGTH)
    is_admin: bool = False

    @field_validator("display_name", mode="before")
    @classmethod
    def strip_display_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone_number(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return normalize_e164_phone_number(value)

    @field_validator("email", "notes", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None


class ContactUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=DISPLAY_NAME_MAX_LENGTH,
    )
    phone_number: str | None = Field(default=None, min_length=2, max_length=16)
    email: EmailStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    notes: str | None = Field(default=None, min_length=1, max_length=NOTES_MAX_LENGTH)
    is_admin: bool | None = None

    @field_validator("display_name", mode="before")
    @classmethod
    def strip_display_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone_number(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return normalize_e164_phone_number(value)

    @field_validator("email", "notes", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_update(self) -> "ContactUpdateRequest":
        if not self.model_fields_set:
            message = "At least one contact field must be provided"
            raise ValueError(message)
        for field_name in ("display_name", "phone_number", "is_admin"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                message = f"{field_name} cannot be null"
                raise ValueError(message)
        return self


class ContactListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contacts: tuple[Contact, ...] = Field(default_factory=tuple)
