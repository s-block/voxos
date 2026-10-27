import { useMemo, useState } from "react";
import type { BrandLogo } from "../api";
import { logoImageUrl, logoInitials } from "../serviceLogos";

type ServiceLogoSize = "sm" | "md" | "lg";

type ServiceLogoProps = {
  className?: string;
  decorative?: boolean;
  label: string;
  logo: BrandLogo | null | undefined;
  size?: ServiceLogoSize;
};

const imageSizes: Record<ServiceLogoSize, number> = {
  lg: 64,
  md: 48,
  sm: 32,
};

export function ServiceLogo({
  className,
  decorative = false,
  label,
  logo,
  size = "md",
}: ServiceLogoProps) {
  const [imageFailed, setImageFailed] = useState(false);
  const displayLabel = logo?.label ?? label;
  const imageUrl = useMemo(
    () =>
      imageFailed
        ? null
        : logoImageUrl(logo, {
            size: imageSizes[size],
          }),
    [imageFailed, logo, size],
  );
  const classNames = [
    "service-logo",
    `service-logo-${size}`,
    imageUrl === null ? "service-logo-fallback" : "",
    className ?? "",
  ]
    .filter((value) => value !== "")
    .join(" ");

  return (
    <span
      aria-hidden={decorative ? "true" : undefined}
      aria-label={decorative ? undefined : `${displayLabel} logo`}
      className={classNames}
      role={decorative ? undefined : "img"}
    >
      {imageUrl === null ? (
        <span aria-hidden="true">{logoInitials(displayLabel)}</span>
      ) : (
        <img
          alt=""
          decoding="async"
          loading="lazy"
          onError={() => setImageFailed(true)}
          src={imageUrl}
        />
      )}
    </span>
  );
}
