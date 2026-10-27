# VoxOS Design Assets

Brand assets for VoxOS, the voice-first agent system. Use the dark lockup on
dark or app-icon-style surfaces, the light lockup directly on white or pale
surfaces, and the compact mark only where the full lockup would become
illegible.

## Files

| File | Use |
| --- | --- |
| `voxos-logo.svg` | Dark square lockup for dark, launch, app-icon-style, or high-contrast surfaces. |
| `voxos-logo-light.svg` | Transparent light-background lockup for white cards, documents, and pale UI surfaces. |
| `voxos-mark.svg` | Compact mark derived from the primary logo for height-restricted surfaces such as nav, favicons, and avatars. |

## Concept

The mark is a speech bubble drawn as an open ring with a pointed tail, with a
five-bar voice waveform rising through its mouth. The dark square logo keeps the
mark above the "VoxOS" wordmark and the "VOICE • ACTION • CONNECTED" tagline on
a dark radial background. The light lockup uses the same layout without the dark
tile: "Vox" switches to ink while "OS" and the mark keep the brand gradient. The
compact mark uses the same bubble and waveform geometry without the wordmark or
tagline.

## Colours

| Token | Value | Use |
| --- | --- | --- |
| Gradient start | `#19F0C4` | Green — top-left of the mark |
| Gradient mid | `#16CFEA` | Cyan — centre of the mark |
| Gradient end | `#6657FF` | Violet — right side of the mark and wordmark |
| Wordmark ink | `#17211C` | "Vox" on light backgrounds |
| Wordmark light | `#F6F7F8` | "Vox" on the dark lockup |
| Tagline grey | `#9AA0AA` | "VOICE" / "CONNECTED" in the primary lockup |
| Tagline ink-muted | `#66736D` | Tagline on light backgrounds |
| Tile dark | `#020407` | Outer dark background |

## Frontend usage

- `frontend/src/components/VoxosMark.tsx` renders `VoxosLogoTile` in light mode
  for the sign-in gate and `VoxosMark` for constrained navigation.
- `frontend/public/favicon.svg` is the mark on a dark rounded tile for tab
  contrast; keep its geometry in sync with `voxos-mark.svg`.
