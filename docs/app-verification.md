# App verification

Derived numbers only. Sections are added by the tasks of `specs/_plans/add-cfb-app`.

## Frontend toolchain (task 1.2, frontend part)

Resolved on 2026-10-23 with the plan's policy: within the planned majors, the newest release at least 7 days old, pinned exactly in `app/frontend/package.json`; `package-lock.json` is committed. The host clock read 2026-10-22 22:27 UTC while the task named 2026-10-23, so the cutoff was set conservatively at 2026-10-15 22:27 UTC (a release that is 7 days old by either reading).

| Package | Pinned | Released (UTC) | Newer exists |
|---|---|---|---|
| react, react-dom | 19.3.1 | 2026-10-14 | no |
| vite | 8.3.4 | 2026-10-08 | 8.3.5 (2026-10-21) |
| @vitejs/plugin-react | 6.1.2 | 2026-10-05 | no |
| typescript | 6.0.3 | 2026-04-16 | 7.0.2 (typescript-eslint 8.x peers `<6.1.0`) |
| vitest | 5.0.5 | 2026-10-15 | no |
| @types/node | 24.19.2 | 2026-10-09 | no |
| jsdom | 30.1.3 | 2026-10-15 | no |
| @testing-library/react / dom / jest-dom / user-event | 16.3.3 / 10.4.2 / 7.0.1 / 14.6.8 | 2026-08-27 / 09-13 / 08-09 / 10-13 | no |
| eslint | 10.12.0 | 2026-10-02 | 10.13.0 (2026-10-16) |
| @eslint/js | 10.0.1 | 2026-02-06 | no (the 10.x line) |
| typescript-eslint | 8.71.2 | 2026-10-12 | 8.72.0 (2026-10-19) |
| eslint-plugin-react-hooks | 7.1.1 | 2026-04-17 | no |
| @types/react, @types/react-dom | 19.3.0 | 2026-09-09 | no |
| globals | 17.13.0 | 2026-10-01 | no |
| ajv (dev; fixture schema check) | 8.20.0 | 2026-04-24 | no |
| @playwright/test | 1.64.1 | 2026-10-14 | no |

Runtime dependencies: `react` and `react-dom` only.

### Where node_modules lives

The repository sits on a OneDrive-synced Windows drive (`/mnt/c/...`). Measured there: `require('jsdom')` took 3 min 28 s, against 2.4 s from the native file system, `npm ci` took about 5 minutes against 17 s, and vitest 5 gave up with "Timeout waiting for worker to respond" (its worker start timeout is a fixed 60 s). `app/frontend/scripts/deps.sh` (`make app-deps`) therefore runs `npm ci` in `~/.cache/cfb-app-frontend` (override: `CFB_FRONTEND_DEPS`) and links `app/frontend/node_modules` to it when the project is under `/mnt/`; elsewhere it installs in place. With the link, `make app-test` takes about 15 s. The link is git-ignored (`app/frontend/node_modules`; the existing `node_modules/` pattern does not match a symlink).

### Playwright gate (Q8, Q19)

Path taken: **(1) local install**. `npx playwright install chromium` ran without `sudo` (Chrome Headless Shell 156.0.8078.4, playwright chromium-headless-shell v1248, into `~/.cache/ms-playwright`), and `npx playwright test` passed `e2e/toolchain.spec.ts` against `vite preview` on 127.0.0.1:4173 (1 passed, 400 ms). The podman image and the API-level fallback were not needed. Side effect: the install pruned the older browser builds (chromium-1223, chromium_headless_shell-1223, ffmpeg-1011) that another Playwright version had cached.

## Palette (task 1.8)

Reference palette of the dataviz skill (`references/palette.md`), light surface `#fcfcfb` on page `#f9f9f7`, dark surface `#1a1a19` on page `#0d0d0d`.

- Odds bars: one sequential hue, blue (`#2a78d6` light, `#3987e5` dark), ramp steps 100/200/300/400/500/600/700.
- Movement: diverging pair blue (up) and red (down): `#2a78d6`/`#e34948` light, `#3987e5`/`#e66767` dark, always with an arrow icon and the word.
- Timeline states: the fixed status palette (`#0ca30c`, `#fab219`, `#ec835a`, `#d03b3b`) with an icon and a word. Warning and serious sit below 3:1 on the light surface (relief rule: icon and label, never color alone); the timeline bars use only good, critical and the accent.
- Waterfall by source: slot 1 blue for LIVE, slot 2 orange for REPLAY (`#eb6834` light, `#d95926` dark), STORED in neutral gray (`#7b7a74` light, `#898781` dark). Two series with a legend and direct labels.
- Text tokens: primary `#0b0b0b`/`#f0efec`, secondary `#52514e`/`#c3c2b7`, muted `#6b6a65`/`#a09e96` (the reference's muted `#898781` is 3.5:1 on the light surface, so it is darkened to clear 4.5:1 for text), accent text `#1c5cab`/`#86b6ef`.

`src/theme/tokens.test.ts` computes WCAG contrast for every text token (at least 4.5:1) and every control and chart color on both the page and the surface (at least 3:1) in both themes, pins the palette, and checks that `theme.css` is the generated output of the tokens.

### Validator output (`validate_palette.js --pairs all`)

```
$ node validate_palette.js "#2a78d6,#eb6834" --mode light --surface "#fcfcfb" --pairs all

Palette (light, surface #fcfcfb, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.43–0.77
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #eb6834↔#2a78d6 ΔE 24.7 (protan) · tritan 32.7
  [PASS] Normal-vision floor    worst all-pairs #eb6834↔#2a78d6 ΔE 33.6 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#3987e5,#d95926" --mode dark --surface "#1a1a19" --pairs all

Palette (dark, surface #1a1a19, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.48–0.67
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #d95926↔#3987e5 ΔE 26.8 (protan) · tritan 32.4
  [PASS] Normal-vision floor    worst all-pairs #d95926↔#3987e5 ΔE 31.8 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#2a78d6,#e34948" --mode light --surface "#fcfcfb" --pairs all

Palette (light, surface #fcfcfb, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.43–0.77
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #e34948↔#2a78d6 ΔE 21.6 (protan) · tritan 34.5
  [PASS] Normal-vision floor    worst all-pairs #e34948↔#2a78d6 ΔE 32.3 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#3987e5,#e66767" --mode dark --surface "#1a1a19" --pairs all

Palette (dark, surface #1a1a19, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.48–0.67
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #e66767↔#3987e5 ΔE 19.2 (protan) · tritan 31.4
  [PASS] Normal-vision floor    worst all-pairs #e66767↔#3987e5 ΔE 29.0 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#2a78d6,#eb6834" --mode light --surface "#f9f9f7" --pairs all

Palette (light, surface #f9f9f7, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.43–0.77
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #eb6834↔#2a78d6 ΔE 24.7 (protan) · tritan 32.7
  [PASS] Normal-vision floor    worst all-pairs #eb6834↔#2a78d6 ΔE 33.6 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#3987e5,#d95926" --mode dark --surface "#0d0d0d" --pairs all

Palette (dark, surface #0d0d0d, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.48–0.67
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #d95926↔#3987e5 ΔE 26.8 (protan) · tritan 32.4
  [PASS] Normal-vision floor    worst all-pairs #d95926↔#3987e5 ΔE 31.8 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

```
