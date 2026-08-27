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

---

# Design QA — QR Menu: ฮง เป็ดย่าง

- Source visual truth: `/Users/tewx/.codex/generated_images/01a0374c-90a3-7c73-9bde-fc042712f651/exec-6ffd5756-9260-4902-a359-4d6ac3563c64.png`
- Implementation screenshot: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/hong-duck-menu-mobile.png`
- Viewport: 390 × 844 CSS px, device scale factor 1
- Source pixels: 853 × 1844
- Implementation full-page pixels: 375 × 2447
- Normalization: compared at matching mobile content width in one visual inspection input; the implementation displays one complete category at a time to keep the page compact.
- State: public unauthenticated menu, ข้าว category selected, fixed call action visible

## Full-view comparison evidence

The implementation retains the selected direction's saturated red canvas, gold/cream type, illustrated food hero, original restaurant logo, three-category control, aligned price hierarchy, and persistent high-contrast call action. The final page includes บะหมี่ and กับข้าว as switchable category views using the same visual system.

## Required fidelity surfaces

- Fonts and typography: bundled Noto Sans Thai 400/500/600/700 is used. The 30–44 px hero hierarchy, 26 px section heading, 16 px menu names, and 23 px prices remain readable at mobile width.
- Spacing and layout rhythm: the 390 px viewport has no horizontal overflow. The sticky categories, 67 px menu rows, 48 px category controls, and 66 px call action preserve clear touch and scan rhythm.
- Colors and visual tokens: the implementation maps the reference to saturated red, deep red, gold, cream, and dark ink tokens with consistent semantic use and strong foreground contrast.
- Image quality and asset fidelity: the 1536 × 1024 generated hero asset is a real raster illustration grounded in the supplied menu artwork and selected concept. The restaurant logo is cropped directly from the supplied original artwork and its red background is removed to sit cleanly over the hero. Both remain sharp at mobile density; no CSS/vector stand-in replaces them.
- Copy and content: restaurant name, branch, phone number, all supplied menu names, base prices, special prices, size prices, and the +10 บาท rule are present. Online ordering and LINE are intentionally absent in phase 1.

## Focused-region comparison evidence

The top viewport was captured at 390 × 844 and checked separately. Restaurant identity, open state with branch beneath it, full-bleed hero crop, category controls, first section heading, and fixed phone action are visible together without clipping. The menu table and call dock were also inspected after switching categories.

## Comparison history

1. P0 runtime: importing the authenticated app initialized Firebase before the public page could render without environment credentials. Fix: split the public menu and authenticated app into conditional dynamic entry modules. Post-fix evidence: the menu renders unauthenticated with no console errors.
2. P0 build: the first conditional entry used top-level await, unsupported by the configured production target. Fix: changed entry loading to promise-based dynamic imports. Post-fix evidence: Vite production build passes.
3. P2 page length: rendering all three complete categories in sequence made the mobile page unnecessarily long. Fix: category controls now swap a single visible menu section while retaining every item and price.
4. P2 brand placement: the logo crop showed its source poster background and competed with the headline. Fix: extracted the dark original logo marks onto transparency and moved the logo below the headline in place of the redundant category copy.
5. P2 pricing consistency: กับข้าว used S/M labels while the other categories used ธรรมดา/พิเศษ. Fix: standardized the table headers and data columns across all three category views.
6. P2 header density: the promotional headline and solid red header delayed the food image. Fix: removed the headline, moved the branch directly beneath the open state, and brought the food illustration to the top edge with the logo overlaid on its clear red area.
7. P1 ordering clarity: phone-only ordering provided no way for customers to remember multiple menu choices. Fix: made both price columns tappable, accumulated repeat taps as quantity, added selected-count badges, and introduced an editable order-summary sheet with quantity, deletion, total, and final call action.
8. P1 LINE ordering: added a clearly separated green `สั่งผ่าน LINE` action above the existing phone action. It opens LINE OA `@862uzpje` with the current item names, variants, quantities, line totals, total quantity, and order total prefilled for the customer to review and send.

## Interaction and runtime checks

- Public route: `/menu/hong-duck` opens without Firebase/Auth initialization.
- Category buttons: ข้าว, บะหมี่, and กับข้าว swap the visible section and update the selected state.
- Phone action: persistent link resolves to `tel:0826516461`.
- Order summary: repeated price taps increment quantity; the fixed action shows total quantity and value; the summary supports add, reduce, delete, empty state, and recalculates totals before calling.
- LINE action: generated URL targets `@862uzpje`, contains the encoded current order summary, and is shown only when the cart has items. The link was inspected without navigating or sending a message.
- Action hierarchy: LINE appears full-width above the full-width phone action at the mobile breakpoint.
- Responsive check: 390 × 844, no horizontal overflow.
- Console: no warnings or errors in a fresh public-menu tab.
- Production build: passed with Vite.

## Findings

No actionable P0, P1, or P2 visual or functional mismatch remains for the phase-1 phone-ordering page.

## Follow-up polish

- P3: replace the temporary outline bird icon with the restaurant's original vector logo when the source logo file becomes available.
- P3: confirm actual opening hours before replacing the current phone-enquiry wording.

final result: passed

---

# Design QA — รายการขาย: item ranking

- Source visual truth: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/receipts-summary-chart-desktop.png`
- Implementation screenshot: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/receipts-sales-items-desktop.png`
- Mobile implementation: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/receipts-sales-items-mobile.png`
- Viewports: 1052 × 885 and 390 × 844 CSS px, device scale factor 1
- State: authenticated owner, weekly period, empty local-emulator sales state

## Evidence and required fidelity surfaces

- Full view: the sales-item table is contained inside the total-sales card and preserves the existing chart and daily-sales hierarchy below it.
- Focused region: column headers for rank, item name, quantity, and revenue remain aligned at desktop and mobile sizes; the 390 px capture has no horizontal overflow.
- Typography: existing Noto Sans Thai weights and compact table sizing are retained.
- Spacing/colors: the table uses the current card, border, accent, and muted-text tokens; no new visual language was introduced.
- Images/icons: no new image or icon assets are required.
- Copy/content: the empty state explains why no rows appear. In populated states every backend `top_items` row renders with its name, quantity, and revenue.

## Comparison history and checks

1. P2: the total-sales card did not explain which items contributed to the total. Fix: added a ranked table inside the card and requested the full item aggregation with `top=0`.
2. P2: monthly item lists can be long. Fix: capped the row region at 292 px with vertical scrolling while keeping the summary visible.
3. Ranking/data check: the existing offline sales-report suite passed 67/67 checks, including aggregation across bills, revenue calculation, and descending quantity order.
4. Production build passed. The browser showed the expected missing-Loyverse-token response for the local review account; no new UI error was introduced.

## Findings

No actionable P0, P1, or P2 issue remains for this scoped change. Populated visual density should be observed again once the review account has real synced sales, but ranking and aggregation are covered by the backend suite.

final result: passed

---

# Design QA — รายการขาย: summary card และ compact chart

- Source visual truth: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/audit-receipts-desktop-after.png`
- Implementation screenshot: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/receipts-summary-chart-desktop.png`
- Mobile implementation: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/receipts-summary-chart-mobile.png`
- Combined comparison: `/Users/tewx/Documents/PROJECT COST CONTROL/POS/POS/frontend/receipts-summary-chart-comparison.png`
- Desktop viewport: 1052 × 885 CSS px, device scale factor 1
- Mobile viewport: 390 × 844 CSS px, device scale factor 1
- Source and implementation pixels: 1052 × 885; no density normalization required
- State: authenticated owner, weekly period, empty local-emulator sales state

## Full-view and focused-region evidence

The side-by-side comparison shows that the total is now a distinct card above the chart while the existing page rhythm, period selector, daily list, and navigation remain unchanged. The chart region was reviewed as the focused area at full resolution: axes, labels, data line, points, trend line, and legend are visibly lighter and smaller without reducing the full-height pointer targets.

## Required fidelity surfaces

- Fonts and typography: the existing Noto Sans Thai family and hierarchy remain. Compact chart labels reduce from 8 to 6.5 SVG units and the legend from 10.5 to 9.
- Spacing and layout rhythm: the summary and chart are now two cards with a 10 px gap. The arrangement collapses without horizontal overflow at 390 px.
- Colors and visual tokens: all new surfaces use existing card, accent, muted-text, and border tokens.
- Image and icon fidelity: this change adds no new raster or icon assets; the existing calendar control is untouched.
- Copy and content: the card states `ยอดขายรวม` and the active period label. Bill count and average-per-bill values remain visible and keep their original data paths.

## Comparison history

1. P2: total, bill count, average, and graph shared one card, weakening the requested total-sales hierarchy. Fix: separated the total metrics into a dedicated card and added the active period to its label.
2. P2: chart strokes and labels remained visually heavy relative to the compact receipts layout. Fix: reduced compact-only height, margins, grid strokes, series/trend strokes, point radii, axis labels, and legend text. Dashboard chart defaults were not changed.
3. Post-fix evidence: desktop comparison and 390 × 844 mobile capture show no clipping or horizontal overflow.

## Interaction and runtime checks

- Active period tested from `สัปดาห์นี้` to `เดือนนี้`; the summary-card label updates with the selection.
- Chart point hit columns and existing day/bill interactions remain in the component paths.
- Browser console contains only the expected missing-Loyverse-token API response from the local review account; no new UI error was introduced.
- Production build passed with Vite.

## Findings

No actionable P0, P1, or P2 issue remains for this scoped change.

final result: passed
