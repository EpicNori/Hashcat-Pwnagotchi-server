# Color Palette

This file is the source of truth for UI and CLI color decisions. Developers should use the semantic CSS variables in `app/static/style.css` and the CLI helpers in `bash/cli_theme.sh` instead of adding one-off colors in components, templates, or scripts.

## Rules

- Use `var(--color-...)` or `var(--process-...)` in CSS after a color has a semantic token.
- Do not add raw hex, `rgb()`, `rgba()`, or named color literals in templates or component CSS.
- CLI scripts must use `bash/cli_theme.sh`. Only `install.sh` and `update.sh` may duplicate ANSI setup because they are bootstrap scripts that can be executed before the repository files exist.
- If a new color is genuinely needed, add its role here, add/update the token in `app/static/style.css`, and update the palette tests.

## Core UI Tokens

| Token | Literal | Role |
| --- | --- | --- |
| `--color-night` | `#07111f` | Dark process backdrop and overlays |
| `--color-ink` | `#102033` | Primary text on light process surfaces |
| `--color-muted` | `#64748b` | Secondary text |
| `--color-muted-strong` | `#475569` | Muted labels that need stronger contrast |
| `--color-nav-ink` | `#333333` | Legacy navbar text and hover fill |
| `--color-surface` | `#fbfdff` | Main process card surface |
| `--color-surface-soft` | `#f4f8fb` | Soft process surface |
| `--color-surface-panel` | `#f8fafc` | Settings panels and info blocks |
| `--color-surface-white` | `#ffffff` | White UI surface |
| `--color-surface-inverse` | `#f6f8fa` | Text on dark process backdrop |
| `--color-border` | `#dbe7f3` | Process borders |
| `--color-border-muted` | `#d9e2ec` | Standard quiet borders |
| `--color-border-subtle` | `#e5e7eb` | Dividers and footer border |
| `--color-border-neutral` | `#dddddd` | Legacy Bootstrap-adjacent borders |
| `--color-brand` | `#0891b2` | Brand cyan and informational progress |
| `--color-action` | `#2563eb` | Primary action blue |
| `--color-action-legacy` | `#2f80ed` | Bootstrap icon/action blue |
| `--color-warning` | `#d97706` | Warning and running state |
| `--color-success` | `#16803c` | Success state |
| `--color-danger` | `#c2410c` | Error and destructive state |

## State And Process Tokens

| Token | Literal | Role |
| --- | --- | --- |
| `--color-status-idle-bg` | `#eef4f8` | Idle status background |
| `--color-status-idle-border` | `#d6e3ec` | Idle status border |
| `--color-status-running-bg` | `#fff7ed` | Running status background |
| `--color-status-running-border` | `#fed7aa` | Running status border |
| `--color-status-success-bg` | `#ecfdf3` | Success status background |
| `--color-status-success-border` | `#bbf7d0` | Success status border |
| `--color-status-danger-bg` | `#fff1f2` | Error status background |
| `--color-status-danger-border` | `#fecdd3` | Error status border |
| `--color-progress-bg` | `#eaf1f7` | Progress track |
| `--color-spinner-bg` | `#eefaff` | Spinner surface |
| `--color-spinner-border` | `#bae6fd` | Spinner border |
| `--color-stepper-idle` | `#cbd8e5` | Idle stepper rail |

## Approved Effects

These `rgba()` values are approved for depth, grids, and overlays only:

- `rgba(255, 255, 255, 0.035)`
- `rgba(219, 231, 243, 0.92)`
- `rgba(0, 0, 0, 0.28)`
- `rgba(16, 32, 51, 0.08)`
- `rgba(7, 17, 31, 0.9)`

## Illustration Asset Colors

The Pwnagotchi SVG uses a small fixed set that must stay documented if it is edited:

- `#101827`
- `#edf3f8`
- `#f8fbfd`
- `#9aa8b6`
- `#d8f7df`

## CLI Palette

The terminal palette is intentionally compact and maps to semantic helper functions:

| Helper | ANSI literal | Role |
| --- | --- | --- |
| `cli_heading`, `cli_info`, `cli_step` | `\033[36m` | Brand cyan and active information |
| `cli_success` | `\033[32m` | Completed successfully |
| `cli_warn` | `\033[33m` | Warning or needs attention |
| `cli_error` | `\033[31m` | Error or failed action |
| `cli_section` | `\033[1m` | Strong headings |
| `cli_kv`, `cli_command` | `\033[2m` | Secondary terminal text |
| Theme reset | `\033[0m` | Reset terminal formatting |

All CLI color output must respect `NO_COLOR`.
