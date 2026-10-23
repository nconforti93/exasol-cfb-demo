# Feature: Home map of FBS teams

The app opens on a map of the United States. Every FBS team's logo sits at its home stadium, so a visitor finds their team the way fans think about it: by place. A conference filter narrows the map and zooms to that conference. Clicking a logo opens the team's page. The map works offline: the US geometry is bundled with the app, and the logos come from the app's own cache (`app/shell`).

## Background

- **Teams on the map** are the FBS teams of the point's season (`CFB_API.TEAM_SEASONS`, classification `fbs`), each with its conference in that season. The location is the team's current home venue (`CFB_API.TEAMS` latitude and longitude, from `CFB_MODEL.VENUE`); a team that moved stadiums is shown at its current one.
- **Projection.** Albers USA, with Alaska and Hawaii as insets (`d3-geo` `geoAlbersUsa().scale(1300).translate([487.5, 305])`, the projection of the bundled `us-atlas` `states-albers-10m.json`, a 975 × 610 view box). A team without coordinates, or whose coordinates fall outside the projection, is not drawn and is listed under "Not on the map".
- **Markers.** Each marker is the team's logo in a disc ringed with the team's color, at a constant size on screen. Without a logo it is the fallback badge (`app/shell`).
- **Overlap.** Markers are laid out by a deterministic relaxation in screen space: a marker that overlaps another is pushed apart, and one moved by more than half its size keeps a leader line to a small dot at its true location. The layout is recomputed when the filter or the zoom changes.
- **Conference filter.** Chips list the season's FBS conferences, plus "All FBS". Choosing one shows only its teams and zooms the view box to fit them, with a minimum box so a small footprint is not over-zoomed. The choice is in the URL (`?conf=<conference>`). Buttons zoom in, out and back to the whole country.
- **Hover and focus** raise a marker and show a card: school, mascot, conference, record at the point and, when ranked, the AP rank.
- **List view.** A switch shows the same teams as a table grouped by conference, with the same links. It is the map's table view and its keyboard path; every marker is also focusable in the map.
- **Season and point.** The map follows the as-of picker (`app/as-of`): its season picks the teams, conferences and records.

## Scenarios

### Scenario: The map shows every FBS team at its home stadium

* *GIVEN* the map fixture: FBS teams with coordinates across the 48 states, one team located in Honolulu, and one FBS team without coordinates
* *WHEN* the home page is opened
* *THEN* the map MUST draw one marker per FBS team with coordinates, the Honolulu team inside the Hawaii inset
* *AND* the team without coordinates MUST be listed under "Not on the map"
* *AND* each marker MUST link to `/team/<team id>` and MUST be reachable by keyboard

### Scenario: Crowded logos are pushed apart and keep a leader line

* *GIVEN* twelve teams whose stadiums lie within 40 km of each other
* *WHEN* the markers are laid out at the national zoom
* *THEN* no two markers MUST overlap by more than 10 % of their size
* *AND* every marker moved by more than half its size MUST draw a leader line to its true location
* *AND* the same input MUST produce the same layout

### Scenario: A conference filter narrows and zooms the map

* *GIVEN* the home page with all FBS teams shown
* *WHEN* the conference chip "Atlantic" is chosen, and then "All FBS"
* *THEN* the map MUST show only Atlantic teams, zoomed to fit them, and the URL MUST carry `conf=Atlantic`
* *AND* after "All FBS" every FBS team MUST be shown again at the national zoom and `conf` MUST leave the URL

### Scenario: The list view is the map's table view

* *GIVEN* the home page
* *WHEN* the list view is switched on
* *THEN* it MUST list the same teams as the map, grouped by conference, each with its record and a link to its page
