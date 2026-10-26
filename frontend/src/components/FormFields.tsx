import { useId } from "react";

type TextFieldProps = {
  disabled: boolean;
  label: string;
  onChange: (value: string) => void;
  placeholder: string;
  type?: "email" | "password" | "tel" | "text";
  value: string;
};

export function TextField({
  disabled,
  label,
  onChange,
  placeholder,
  type = "text",
  value,
}: TextFieldProps) {
  const fieldId = useId();

  return (
    <label className="field" htmlFor={fieldId}>
      <span>{label}</span>
      <input
        disabled={disabled}
        id={fieldId}
        onChange={(event) => onChange(event.currentTarget.value)}
        placeholder={placeholder}
        type={type}
        value={value}
      />
    </label>
  );
}

type SelectOption = {
  disabled?: boolean;
  label: string;
  value: string;
};

type SelectFieldProps = {
  disabled: boolean;
  label: string;
  onChange: (value: string) => void;
  options: readonly SelectOption[];
  value: string;
};

export function SelectField({
  disabled,
  label,
  onChange,
  options,
  value,
}: SelectFieldProps) {
  const fieldId = useId();

  return (
    <label className="field" htmlFor={fieldId}>
      <span>{label}</span>
      <select
        disabled={disabled}
        id={fieldId}
        onChange={(event) => onChange(event.currentTarget.value)}
        value={value}
      >
        {options.map((option) => (
          <option
            disabled={option.disabled}
            key={option.value}
            value={option.value}
          >
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

type TextAreaFieldProps = {
  disabled: boolean;
  label: string;
  onChange: (value: string) => void;
  placeholder: string;
  value: string;
};

export function TextAreaField({
  disabled,
  label,
  onChange,
  placeholder,
  value,
}: TextAreaFieldProps) {
  const fieldId = useId();

  return (
    <label className="field" htmlFor={fieldId}>
      <span>{label}</span>
      <textarea
        disabled={disabled}
        id={fieldId}
        onChange={(event) => onChange(event.currentTarget.value)}
        placeholder={placeholder}
        value={value}
      />
    </label>
  );
}
