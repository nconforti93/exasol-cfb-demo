# Feature: App shell: serving, layout, themes and credit

The frame around the pages. One process serves the app and its API, so the same build runs on a laptop or next to the AWS database. The app opens on a map of the teams (`app/map`); a header leads to each team's page (`app/team`), the league tabs (`app/league`) and head-to-head. Team logos come from the app's own cache, never from another site, so the demo runs offline. Every page carries the CFBD credit. Text stays legible on a projector in both themes. A database outage shows a clear message instead of a broken page. A slot for the chat panel is reserved for Phase 7, so adding the chat later does not mean reworking the layout.

## Background

- **Production: `make app-serve`.** One uvicorn process with one worker serves both `/api/*` and the built frontend from `app/frontend/dist`.
  - The client routes (`/`, `/team/<id>`, `/league/<tab>`, `/head-to-head`, and the old `/odds`, `/predictions`, `/upsets`) answer with `index.html`. The old paths are replaced in the browser by their `/league/` path (`app/league`).
  - An unknown path under `/api/` answers 404 with JSON.
  - Hashed assets are served with a long cache lifetime; `index.html` with `no-cache`.
  - **Response headers:**
    - `Content-Security-Policy`: only the app's own origin for scripts, styles and connections; images from the app itself and `data:`; `frame-ancestors 'none'`;
    - `X-Content-Type-Options: nosniff`;
    - `Referrer-Policy: same-origin`.
  - **Nothing the policy would block.** `index.html` has no inline script: the theme is set before the first paint by a small same-origin script file, so the page does not flash in the wrong theme. The app loads no font, style or script from another host; text uses the system's font stack.
- **Team logos.**
  - `make logos` downloads each FBS team's logos once, from the URLs of the stored team list, into a cache outside the repository and outside OneDrive (default `~/.cfb-demo/logos/<team id>.png` and `<team id>-dark.png`). It is an external fetch to the logo host, not a CFBD call; it costs no quota and never runs from the app.
  - The app serves them at `/api/logos/<team id>.png` (`?variant=dark` for the dark theme, falling back to the light file). Team responses say whether a logo exists (`has_logo`), so the browser asks only for logos that exist.
  - Without a logo, or with `APP_LOGOS=off` (for recordings that must not show team marks), a team is drawn as a badge: a disc in the team's color with its abbreviation in a text color that reaches 4.5:1 against it, ringed in the alternate color.
  - Logos are never committed (team marks appear in private media only).
- **Development: `make app`.** It runs the backend with reload and the Vite dev server, which forwards `/api` to the backend. Cross-origin requests are allowed only from the dev server's origins (`app/access`).
- **Layout.**
  - A header with the app's name, the navigation (Map, League, Head-to-head), a team search, the as-of picker, the quota badge, the refresh control, the theme switch and the inspector switch. The League page has its own tabs (`app/league`).
  - The page, and the inspector drawer.
  - The footer.
  - A region reserved for the chat panel.
- **The footer** is on every page. It reads "Data provided by CollegeFootballData.com", linked to that site, and states that the data is not official NCAA or CFP data (mission constraints). It also names the deployment (`local` or `aws`) and the fetch mode most recently seen.
- **Themes.** The app has a light and a dark theme.
  - It follows the system's preference unless the user picks one.
  - The choice is remembered in the browser when storage works. The app never depends on that storage.
- **Projector legibility.**
  - Body text is at least 18 px, and numbers use tabular figures.
  - In both themes, text contrast is at least 4.5:1 and the contrast of controls and chart marks against the page is at least 3:1.
  - **Charts follow the dataviz method:**
    - a palette that passes the validator in both themes;
    - states shown with an icon and a word, never by color alone;
    - a table view for every chart;
    - no dual axes.
- **A database outage.** API calls answer 503 with JSON, for example "Exasol unreachable at <dsn> (target <name>)", with no driver text (`vs/host/target.py`). The pages show a banner with a retry button. The server keeps running and recovers when the database is back.
- **The chat slot (Phase 7).**
  - The app keeps its state in one store: the point, the page, the focus team (which is also the team page's team), the map's conference filter, the what-if conditions and the inspector's state. The store has actions that set each of them, and the chat's app-state tools will use them.
  - The chat region renders only when `GET /api/config` reports the chat as enabled, which it never does in this phase.
  - The path `/api/chat/` is reserved and answers 404.

## Scenarios

### Scenario: One process serves the app and its API

* *GIVEN* a built frontend and the backend on the app test stack
* *WHEN* a client requests `/`, `/team/102`, `/league/odds`, `/head-to-head`, an asset file named in `index.html`, `/api/config` and `/api/unknown`
* *THEN* `/`, `/team/102`, `/league/odds` and `/head-to-head` MUST return `index.html`, and the asset MUST return its file with a long cache lifetime
* *AND* `/api/config` MUST return JSON, and `/api/unknown` MUST return 404 with JSON
* *AND* every response MUST carry the content security policy, `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`
* *AND* `index.html` MUST contain no inline script, and every script, style and font it names MUST be served by the app itself

### Scenario: The footer credits CFBD on every page

* *GIVEN* the app rendered on the map, a team page, each league tab and head-to-head
* *WHEN* the footer is read
* *THEN* it MUST contain "Data provided by CollegeFootballData.com", linked to `https://collegefootballdata.com`
* *AND* it MUST state that the data is not official NCAA or CFP data

### Scenario: Team logos come from the app, with a badge when one is missing

* *GIVEN* a logo cache holding Brookvale's logo but not Ashford's, and the backend serving it
* *WHEN* the map and Brookvale's page are rendered, and `/api/logos/102.png`, `/api/logos/101.png` and `/api/logos/x.png` are requested
* *THEN* Brookvale MUST be drawn with its logo from `/api/logos/102.png`, and Ashford MUST be drawn as a badge with its abbreviation, without a request for its logo
* *AND* `/api/logos/102.png` MUST return the PNG, `/api/logos/101.png` MUST return 404 with JSON, and `/api/logos/x.png` MUST return 400 before any file is read
* *AND* no request MUST go to a host other than the app's own

### Scenario: Both themes keep text legible on a projector

* *GIVEN* the light and the dark theme tokens
* *WHEN* the contrast of every text token against its background, and of every control and chart color against the page, is computed
* *THEN* every text pair MUST reach 4.5:1, every control and chart color MUST reach 3:1, and the base text size MUST be at least 18 px
* *AND* switching the theme MUST change the page's theme and keep it after a reload, and MUST still work when the browser's storage throws

### Scenario: A database outage shows a clear state, not a crash

* *GIVEN* the backend started on the app test stack, after which its database becomes unreachable (a target address with no database behind it)
* *WHEN* a page loads its data, and then retries after the database is reachable again
* *THEN* the API MUST answer 503 with a message that names the address and the target and contains no driver text
* *AND* the page MUST show a banner with a retry button instead of the page
* *AND* after the retry the page MUST show its data again, without a restart of the server

### Scenario: The chat slot receives the app state

* *GIVEN* the app rendered with a configuration that enables the chat, as in a test, and once with the configuration of this phase
* *WHEN* the app is at 2026 week 9 on the odds page, with Brookvale focused and "Brookvale wins at Ashford" set, and the slot then calls the store's action that clears the conditions
* *THEN* the slot MUST receive the point, the page, the focus team and the conditions
* *AND* after the slot's action, the odds page MUST show no what-if condition set
* *AND* with this phase's configuration, the app MUST NOT render the chat region
