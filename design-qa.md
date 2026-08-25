# Design QA — ล้านครัว visual direction 2

- Source visual truth: `/Users/tewx/.codex/generated_images/01a0374c-90a3-7c73-9bde-fc042712f651/exec-75d398f9-e3f5-4a56-b9bb-a4739c5c1d56.png`
- Implementation screenshot: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/implementation-dashboard.png`
- Combined comparison: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/design-qa-comparison.png`
- Viewport: 1440 × 1024 CSS px, device scale factor 1
- Source pixels: 1487 × 1058
- Implementation pixels: 1440 × 1024
- Normalization: both images scaled to 720 × 512 and placed side by side without crop
- State: authenticated owner, dashboard, empty local-emulator sales state, one stock-count alert

## Full-view comparison evidence

The implementation preserves the selected direction's ivory canvas, narrow warm sidebar, terracotta active/action color, cocoa typography, restrained green success color, horizontal alert treatment, large sales figure, and lightweight divider-led layout. The source contains populated sales and top-menu data; the implementation screenshot uses a local empty state because no Loyverse account was connected. This is an expected data-state difference rather than a UI omission: the existing `TopItems` surface renders when data is present.

## Required fidelity surfaces

- Fonts and typography: Noto Sans Thai is bundled locally at weights 400/500/600/700 and applied across the app. Body text remains legible at 14–16 px with a 28–42 px display hierarchy. The sidebar is slightly more compact than the concept to retain every existing navigation label.
- Spacing and layout rhythm: sidebar/main proportions, top header, horizontal divider, alert row, segmented period control, and sales region align with the source. Existing extra account and branch context remain visible because they are functional product information.
- Colors and tokens: brand palette maps to reusable CSS tokens (`--accent`, warm surfaces, cocoa text, green success, amber warning) with AA-friendly primary text contrast.
- Image and icon fidelity: no raster imagery is required by the selected app screen. Emoji navigation was replaced with a consistent Phosphor outline icon family; the bowl/steam brand mark uses the same icon system.
- Copy and content: existing Thai product copy and every route are retained. Dashboard heading and brand copy follow the chosen direction.

## Focused-region comparison evidence

A separate crop was not needed: at the normalized 720 × 512 per-side size, the sidebar, header, alert region, sales hierarchy, icons, and primary controls are all readable in the combined comparison. Modal and responsive states were tested separately in-browser.

## Comparison history

1. Initial implementation finding — P2: the sales chart expanded across the entire main canvas and became substantially taller than the source. Fix: added a responsive dashboard sales grid, constrained the chart on desktop, and reserved the adjacent region for `TopItems` when populated. Post-fix evidence: `implementation-dashboard.png` and `design-qa-comparison.png`.
2. Initial implementation finding — P2: mixed emoji/system symbols weakened brand consistency. Fix: introduced one Phosphor outline icon family for navigation, brand, dashboard alerts, refresh, and date controls. Post-fix evidence: final sidebar and dashboard screenshots.
3. Initial implementation finding — P2: mobile hierarchy and navigation touch targets needed verification. Fix: added 40–44 px controls, focus-visible states, a four-item bottom bar, 390 px responsive rules, and checked all four tab rectangles remain inside the viewport.
4. Follow-up finding — P1: the empty local sales state removed the `TopItems` component, causing the chart to occupy the layout alone and drift from the selected two-column concept. Fix: the dashboard now always renders the two-column sales/menu structure on desktop; `TopItems` shows a purposeful empty state when no sales exist and populates from the original data path when sales are available.
5. Follow-up finding — P2: the system fallback font did not match the selected visual closely enough across operating systems. Fix: bundled Noto Sans Thai in four weights and applied it globally, removing dependence on the viewer's installed fonts.

## Interaction and runtime checks

- Routes checked: `/`, `/materials`, `/receiving`, `/stock-count`, `/receipts`, `/income-expense`, `/recipes`, `/items`, `/users`, `/settings`, `/more`.
- Primary interactions checked: period selection, setup navigation expansion, add-material modal open and cancel, desktop sidebar, and mobile bottom navigation.
- Responsive checks: 1440 × 1024 desktop and 390 × 844 mobile.
- Console: no UI implementation errors. Expected local-emulator API errors were limited to missing Loyverse connection; existing React Router future-version warnings remain.
- Production build: passed with Vite.

## Findings

No actionable P0, P1, or P2 visual mismatch remains. Populated-data density should be rechecked later with a real connected store, but current components retain the complete existing rendering paths.

## Follow-up polish

- P3: replace remaining inline warning/check glyphs inside deep form validation states with the shared icon family during a later component-cleanup pass.
- P3: consider bundling a dedicated Thai webfont after product performance requirements are known.

final result: passed
