import { useId } from "react";

type VoxosMarkProps = {
  ariaLabel?: string;
  className?: string;
  size?: number;
  title?: string;
};

type VoxosLogoTileProps = {
  ariaLabel?: string;
  className?: string;
  size?: number;
  surface?: "dark" | "light";
  title?: string;
};

function useUniqueSvgId(name: string): string {
  return `${useId().replaceAll(":", "")}-${name}`;
}

/** Compact mark derived from docs/design/voxos-logo.svg for constrained UI. */
export function VoxosMark({
  ariaLabel,
  className,
  size = 24,
  title,
}: VoxosMarkProps) {
  const gradientId = useUniqueSvgId("voxos-mark-gradient");
  const label = ariaLabel ?? title;

  return (
    <svg
      aria-hidden={label === undefined ? "true" : undefined}
      aria-label={ariaLabel}
      className={className}
      height={size}
      role={label === undefined ? undefined : "img"}
      viewBox="5 5 110 110"
      width={size}
      xmlns="http://www.w3.org/2000/svg"
    >
      {title !== undefined ? <title>{title}</title> : null}
      <defs>
        <linearGradient
          gradientUnits="userSpaceOnUse"
          id={gradientId}
          x1="34"
          y1="28"
          x2="103"
          y2="84"
        >
          <stop offset="0%" stopColor="#19f0c4" />
          <stop offset="46%" stopColor="#16cfea" />
          <stop offset="100%" stopColor="#6657ff" />
        </linearGradient>
      </defs>
      <path
        d="M 31.25 80 L 18.5 92 L 23.5 75.25 A 44.5 44.5 0 1 1 102 79.25"
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="9.5"
      />
      <g fill={`url(#${gradientId})`}>
        <rect height="29.5" rx="3" width="6" x="39.5" y="54.5" />
        <rect height="39.25" rx="3" width="6" x="50.5" y="48.5" />
        <rect height="50.5" rx="3" width="6" x="61.5" y="40.75" />
        <rect height="37.5" rx="3" width="6" x="72.5" y="50" />
        <rect height="24" rx="3" width="6" x="83.5" y="57" />
      </g>
    </svg>
  );
}

/** Full square lockup from docs/design/voxos-logo.svg for roomy brand surfaces. */
export function VoxosLogoTile({
  ariaLabel,
  className,
  size,
  surface = "dark",
  title,
}: VoxosLogoTileProps) {
  const bgGlowId = useUniqueSvgId("voxos-bg-glow");
  const brandGradientId = useUniqueSvgId("voxos-brand-gradient");
  const wordGradientId = useUniqueSvgId("voxos-word-gradient");
  const softGlowId = useUniqueSvgId("voxos-soft-glow");
  const label = ariaLabel ?? title;
  const isDarkSurface = surface === "dark";

  return (
    <svg
      aria-hidden={label === undefined ? "true" : undefined}
      aria-label={ariaLabel}
      className={className}
      height={size}
      role={label === undefined ? undefined : "img"}
      viewBox={isDarkSurface ? "100 170 1000 1000" : "120 220 1040 730"}
      width={size}
      xmlns="http://www.w3.org/2000/svg"
    >
      {title !== undefined ? <title>{title}</title> : null}
      <defs>
        <radialGradient id={bgGlowId} cx="50%" cy="42%" r="55%">
          <stop offset="0%" stopColor="#0b2225" stopOpacity="0.9" />
          <stop offset="55%" stopColor="#060b10" stopOpacity="1" />
          <stop offset="100%" stopColor="#020407" stopOpacity="1" />
        </radialGradient>
        <linearGradient
          gradientUnits="userSpaceOnUse"
          id={brandGradientId}
          x1="270"
          x2="920"
          y1="240"
          y2="760"
        >
          <stop offset="0%" stopColor="#19f0c4" />
          <stop offset="46%" stopColor="#16cfea" />
          <stop offset="100%" stopColor="#6657ff" />
        </linearGradient>
        <linearGradient
          gradientUnits="userSpaceOnUse"
          id={wordGradientId}
          x1="665"
          x2="925"
          y1="720"
          y2="850"
        >
          <stop offset="0%" stopColor="#18e9d0" />
          <stop offset="100%" stopColor="#5d63ff" />
        </linearGradient>
        <filter id={softGlowId} x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="10" result="blur" />
          <feColorMatrix
            in="blur"
            result="glow"
            type="matrix"
            values="0 0 0 0 0.03  0 0 0 0 0.95  0 0 0 0 0.95  0 0 0 0.45 0"
          />
          <feMerge>
            <feMergeNode in="glow" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {isDarkSurface ? (
        <rect width="1200" height="1200" fill={`url(#${bgGlowId})`} />
      ) : null}

      <g
        filter={isDarkSurface ? `url(#${softGlowId})` : undefined}
        transform="translate(0 8)"
      >
        <path
          d="M475 622 L424 672 L444 603 A178 178 0 1 1 758 619"
          fill="none"
          stroke={`url(#${brandGradientId})`}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="38"
        />
        <g fill={`url(#${brandGradientId})`}>
          <rect height="118" rx="12" width="24" x="508" y="520" />
          <rect height="157" rx="12" width="24" x="552" y="496" />
          <rect height="202" rx="12" width="24" x="596" y="465" />
          <rect height="150" rx="12" width="24" x="640" y="502" />
          <rect height="96" rx="12" width="24" x="684" y="530" />
        </g>
      </g>

      <g
        fill={isDarkSurface ? "#f6f7f8" : "#17211c"}
        fontFamily="Inter, Manrope, Avenir Next, Helvetica Neue, Arial, sans-serif"
        fontSize="174"
        fontWeight="800"
        letterSpacing="-18"
      >
        <text x="318" y="829">
          Vox
        </text>
        <text fill={`url(#${wordGradientId})`} x="667" y="829">
          OS
        </text>
      </g>

      <g
        fontFamily="Inter, Manrope, Avenir Next, Helvetica Neue, Arial, sans-serif"
        fontSize="28"
        fontWeight="600"
        letterSpacing="11"
      >
        <text x="341" y="902" fill={isDarkSurface ? "#9aa0aa" : "#66736d"}>
          VOICE
        </text>
        <circle cx="538" cy="892" r="5.5" fill="#14e2d4" />
        <text x="574" y="902" fill="#1be7d1">
          ACTION
        </text>
        <circle cx="739" cy="892" r="5.5" fill="#4e78ff" />
        <text x="776" y="902" fill={isDarkSurface ? "#9aa0aa" : "#66736d"}>
          CONNECTED
        </text>
      </g>
    </svg>
  );
}
