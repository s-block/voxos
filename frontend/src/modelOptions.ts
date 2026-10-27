type CatalogModelOption = {
  model: string;
  label: string;
  recommended: boolean;
};

export type ModelSelectOption = {
  label: string;
  value: string;
};

export function modelIds(modelOptions: readonly CatalogModelOption[]): string[] {
  return modelOptions.map((option) => option.model);
}

export function selectableModelOptions(
  selectedValue: string,
  modelOptions: readonly CatalogModelOption[],
  defaultValue: string,
  fallbackValues: readonly string[] = [],
): ModelSelectOption[] {
  const metadataByModel = new Map(
    modelOptions.map((option) => [option.model, option]),
  );
  return uniqueModelValues(
    selectedValue,
    defaultValue,
    ...modelIds(modelOptions),
    ...fallbackValues,
  ).map((value) => {
    const metadata = metadataByModel.get(value);
    return {
      label:
        metadata === undefined
          ? value
          : `${metadata.label}${metadata.recommended ? " · Recommended" : ""}`,
      value,
    };
  });
}

function uniqueModelValues(...values: string[]): string[] {
  return Array.from(
    new Set(values.map((value) => value.trim()).filter((value) => value !== "")),
  );
}
