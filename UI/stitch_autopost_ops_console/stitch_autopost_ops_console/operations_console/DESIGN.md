---
name: Operations Console
colors:
  surface: '#f7f9fb'
  surface-dim: '#d8dadc'
  surface-bright: '#f7f9fb'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f4f6'
  surface-container: '#eceef0'
  surface-container-high: '#e6e8ea'
  surface-container-highest: '#e0e3e5'
  on-surface: '#191c1e'
  on-surface-variant: '#45464d'
  inverse-surface: '#2d3133'
  inverse-on-surface: '#eff1f3'
  outline: '#76777d'
  outline-variant: '#c6c6cd'
  surface-tint: '#565e74'
  primary: '#000000'
  on-primary: '#ffffff'
  primary-container: '#131b2e'
  on-primary-container: '#7c839b'
  inverse-primary: '#bec6e0'
  secondary: '#505f76'
  on-secondary: '#ffffff'
  secondary-container: '#d0e1fb'
  on-secondary-container: '#54647a'
  tertiary: '#000000'
  on-tertiary: '#ffffff'
  tertiary-container: '#271901'
  on-tertiary-container: '#98805d'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dae2fd'
  primary-fixed-dim: '#bec6e0'
  on-primary-fixed: '#131b2e'
  on-primary-fixed-variant: '#3f465c'
  secondary-fixed: '#d3e4fe'
  secondary-fixed-dim: '#b7c8e1'
  on-secondary-fixed: '#0b1c30'
  on-secondary-fixed-variant: '#38485d'
  tertiary-fixed: '#fcdeb5'
  tertiary-fixed-dim: '#dec29a'
  on-tertiary-fixed: '#271901'
  on-tertiary-fixed-variant: '#574425'
  background: '#f7f9fb'
  on-background: '#191c1e'
  surface-variant: '#e0e3e5'
typography:
  display:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
  code:
    fontFamily: jetbrainsMono
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  sidebar_width: 240px
  container_max_width: 1280px
  gutter: 24px
  margin_mobile: 16px
  margin_desktop: 32px
  stack_sm: 8px
  stack_md: 16px
  stack_lg: 24px
---

## Brand & Style
The design system is engineered for high-velocity media operations, prioritizing utility and clarity over decorative flair. It targets power users who manage automated news pipelines, requiring a UI that feels like a precision tool rather than a consumer app. 

The aesthetic is **Refined Minimalism**, drawing inspiration from high-end productivity software. It utilizes a restrained palette, strict grid alignment, and purposeful whitespace to reduce cognitive load. There are no gradients or "AI" clichés; the intelligence of the platform is conveyed through the order and efficiency of its interface. The emotional goal is to evoke a sense of calm control and professional reliability.

## Colors
This design system employs a "Slate & Snow" palette. The background uses a very light neutral (`#F8FAFC`) to provide a subtle contrast against white surface containers, creating a clear hierarchical distinction without heavy shadows.

- **Primary:** Deep slate (`#0F172A`) for primary actions and text to ensure maximum legibility.
- **Secondary:** Medium gray (`#64748B`) for secondary actions, icons, and metadata.
- **Neutral:** A range of slates for borders, backgrounds, and subtle fills.
- **Status Colors:** Pure, high-contrast tints used sparingly for state indicators (e.g., "Published", "Draft", "Error") to ensure they remain functional "beacons" within a monochromatic environment.

## Typography
The system relies exclusively on **Inter** to maintain a systematic, utilitarian feel. The hierarchy is established through weight and subtle shifts in letter spacing rather than dramatic size changes. 

- **Headlines:** Semi-bold with tight letter spacing for a compact, "engineered" look.
- **Body:** Standardized at 14px for density without sacrificing readability.
- **Labels:** Used for navigation and tags, often in Medium weight (500) to distinguish them from body copy.
- **Monospace:** JetBrains Mono is used for data identifiers, logs, and system paths.

## Layout & Spacing
The layout follows a **Fixed-Sidebar/Fluid-Content** model. 

- **Sidebar:** A persistent 240px left-hand navigation allows for rapid context switching.
- **Grid:** A 12-column grid is used for dashboard views. Content typically resides within white "cards" or "panels" that span 4, 6, or 12 columns.
- **Rhythm:** Spacing follows an 8px base unit. Generous internal padding (24px) within panels ensures that dense news data remains digestible.
- **Breakpoints:** On tablets, the sidebar collapses into a drawer. On mobile, the grid collapses into a single-column vertical stack with 16px horizontal margins.

## Elevation & Depth
Depth is created through **Tonal Layering** and **Fine Outlines** rather than heavy shadows.

1.  **Level 0 (Canvas):** The base background (`#F8FAFC`).
2.  **Level 1 (Panels/Cards):** White surfaces (`#FFFFFF`) with a 1px border (`#E2E8F0`). This is the primary work surface.
3.  **Level 2 (Modals/Popovers):** White surfaces with a very subtle, diffused shadow (0px 4px 12px rgba(0,0,0,0.05)) to suggest they are floating above the workspace.

Interactions (hovers) should use subtle background fills (e.g., `#F1F5F9`) instead of elevation changes to maintain the flat, professional aesthetic.

## Shapes
The design system uses a strictly disciplined radius of **6px to 8px** (Soft). This provides a professional balance—modern enough to feel current, but sharp enough to appear precise. 

- **Components:** Buttons, inputs, and small cards use a 6px radius.
- **Containers:** Main dashboard panels and modals use an 8px radius.
- **Circular elements:** Only used for user avatars or specific status pips; otherwise, avoid "pill" shapes for buttons to maintain the rectangular, structured layout.

## Components
- **Buttons:** Primary buttons are solid Slate (`#0F172A`) with white text. Secondary buttons use a white background with a 1px slate-200 border. No gradients or inner glows.
- **Input Fields:** Minimalist design with a 1px border. Focus state is indicated by a 1px Slate-900 border and a subtle 2px focus ring of Slate-100.
- **Chips/Badges:** Used for status. Small text, medium weight, with a very light background tint (e.g., Success is Emerald-50 background with Emerald-700 text).
- **Navigation:** The sidebar uses high-contrast text for active states and a subtle background highlight. Icons should be 20px, stroke-based (2px weight), and monochromatic.
- **Data Tables:** Borderless rows with a 1px bottom divider. Header cells use `label-sm` typography with uppercase styling and a light gray background.
- **Cards:** White background, 1px border, no shadow. Used to group related news metrics or configuration settings.