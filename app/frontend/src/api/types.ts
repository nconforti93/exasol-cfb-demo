/**
 * The API contract the frontend consumes (plan.md, Key Interfaces: HTTP API).
 *
 * Keys are the serving views' columns in lower case. This file is the source of the fixture
 * schema: `npm run schema` derives `src/test/fixtures/contract.schema.json` from the exported
 * interfaces and type aliases, and `app/backend/models.py` must parse every fixture
 * (`tests/unit/test_app_fixtures.py`, driven by `src/test/fixtures/manifest.json`).
 * Keep it to plain interfaces, string unions, arrays and `Record<string, T>`.
 */

export type FetchMode = 'LIVE' | 'REPLAY'
export type SeasonType = 'regular' | 'postseason'
export type Population = 'FBS_INVOLVED' | 'FBS_VS_FBS'
export type FreezeKind = 'IN_ADVANCE' | 'RECONSTRUCTED' | 'ALL'
export type Move = 'UP' | 'DOWN' | 'SAME' | 'NEW'
export type ProbSource = 'LINE' | 'ELO'
export type SiteKind = 'HOME' | 'AWAY' | 'NEUTRAL'
export type SpendSourceKind = 'LIVE' | 'REPLAY' | 'STORED' | 'NONE'
export type RunStatus = 'RUNNING' | 'DONE' | 'FAILED' | 'ABANDONED'
export type StepStatus = 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED' | 'SKIPPED'
export type Principal = 'app' | 'reader' | 'chat'

/** Error codes of `{error, message}` bodies, by status (plan.md, HTTP API). */
export type ApiErrorCode =
  | 'bad_host'
  | 'invalid_argument'
  | 'condition_error'
  | 'forbidden'
  | 'operator_disabled'
  | 'console_disabled'
  | 'not_found'
  | 'max_calls_exceeded'
  | 'ledger_refused'
  | 'quota_refused'
  | 'live_busy'
  | 'live_disabled'
  | 'reconcile_pending'
  | 'refresh_locked'
  | 'refresh_running'
  | 'whatif_run_changed'
  | 'cfbd_failed'
  | 'database_unreachable'
  | 'busy'

export interface ApiErrorBody {
  error: ApiErrorCode
  message: string
  calls_charged?: number | null
}

export interface Notice {
  code: string
  message: string
}

/* config, health */

export interface Capability {
  enabled: boolean
  reason: string | null
}

export interface ChatConfig {
  available: boolean
  enabled: boolean
  reason: string | null
  model: string | null
}

export interface LedgerConfig {
  on: boolean
  plan: string | null
  plan_cap: number | null
  plan_room: number | null
  month: string | null
  month_cap: number | null
  month_room: number | null
}

export interface LiveConfig {
  enabled: boolean
  reasons: string[]
}

export interface ConfigResponse {
  version: string
  target: string
  dev: boolean
  fetch_mode: FetchMode | null
  operator: Capability
  console: Capability
  ledger: LedgerConfig
  live: LiveConfig
  read_check_skipped: boolean
  chat: ChatConfig
}

export interface HealthResponse {
  ok: boolean
}

/* points */

export interface CurrentPoint {
  as_of_season: number
  as_of_week: number | null
  as_of_season_type: SeasonType
  as_of_week_seq: number
  next_week_seq: number | null
  as_of_label: string
  publication_id: number | null
  whatif_publication_id: number | null
  whatif_run_id: number | null
  whatif_as_of_week_seq: number | null
  whatif_views_current: boolean
}

export interface AsOfPoint {
  as_of_season: number
  as_of_week: number | null
  as_of_season_type: SeasonType
  as_of_week_seq: number
  next_week_seq: number | null
  as_of_label: string
  is_current: boolean
  is_after_current: boolean
  has_publication: boolean
  has_predictions: boolean
  has_grades: boolean
}

/** The point a page was answered at: the requested one, or the current one with a notice. */
export interface ResolvedPoint {
  season: number
  week_seq: number
  next_week_seq: number | null
  label: string
  is_current: boolean
}

export interface PointsResponse {
  current: CurrentPoint
  season: number
  seasons: number[]
  points: AsOfPoint[]
  notice: Notice | null
}

/** A team with what its mark needs: colors as CFBD sends them, and whether the app's logo cache holds its logo. */
export interface Team {
  team_id: number
  school: string
  conference: string | null
  abbreviation: string | null
  color: string | null
  alternate_color: string | null
  has_logo: boolean
}

export interface TeamsResponse {
  teams: Team[]
}

/* odds */

export interface OddsRow {
  odds_rank: number
  team_id: number
  school: string
  conference: string | null
  p_in_cfp: number
  p_bye: number
  p_quarterfinal: number
  p_semifinal: number
  p_final: number
  p_champion: number
  p_conf_champion: number | null
  expected_wins: number
  mean_seed: number | null
  prev_as_of_label: string | null
  prev_p_in_cfp: number | null
  delta_p_in_cfp: number | null
  delta_p_champion: number | null
  rank_change: number | null
  move: Move
}

export interface RunBanner {
  run_id: number
  publication_id: number
  run_origin: string
  n_sims: number
  seconds_total: number
  sims_per_second: number | null
  nodes: number | null
  instances: number | null
  published_at: string
}

export interface WhatIfAvailability {
  available: boolean
  reason: 'run_at_other_point' | 'regenerating' | 'no_run' | null
  message: string | null
  run_id: number | null
  run_label: string | null
}

export interface OddsResponse {
  point: ResolvedPoint
  notice: Notice | null
  rows: OddsRow[]
  run: RunBanner | null
  move_threshold: number
  whatif: WhatIfAvailability
}

/* what-if and rooting guide */

export interface WhatIfRun {
  run_id: number
  season: number
  as_of_week_seq: number
  as_of_label: string
  n_sims: number
}

export interface WhatIfGame {
  game_id: number
  season: number
  week_seq: number
  is_next_week: boolean
  start_at: string | null
  home_team_id: number
  home_school: string
  away_team_id: number
  away_school: string
  home_win_prob: number
}

export interface WhatIfGamesResponse {
  run: WhatIfRun | null
  games: WhatIfGame[]
  max_conditions: number
}

export type ConditionWinner = 'home' | 'away'

export interface WhatIfCondition {
  game_id: number
  winner: ConditionWinner
}

export interface WhatIfOddsRequest {
  run_id: number
  conditions: WhatIfCondition[]
}

export interface WhatIfOddsRow {
  team_id: number
  school: string
  conference: string | null
  p_in_cfp: number
  p_champion: number
  base_p_in_cfp: number
  base_p_champion: number
  delta_p_in_cfp: number
  delta_p_champion: number
  low_sample: boolean
}

export interface WhatIfOddsResponse {
  run_id: number
  n_sims: number
  effective_sample: number
  low_sample: boolean
  method: string
  rows: WhatIfOddsRow[]
}

export interface GuideRow {
  game_id: number
  season: number
  week_seq: number
  is_next_week: boolean
  guide_rank: number | null
  next_week_rank: number | null
  home_team_id: number
  home_school: string
  away_team_id: number
  away_school: string
  root_for_team_id: number
  root_for_school: string
  p_if_home_win: number
  p_if_away_win: number
  leverage: number
  low_sample: boolean
}

export interface GuideResponse {
  run_id: number | null
  team_id: number
  conditional: boolean
  top: GuideRow[]
  coming_week: GuideRow[]
  message: string | null
}

export interface WhatIfGuideRequest {
  run_id: number
  team_id: number
  conditions: WhatIfCondition[]
}

/* predictions and scorecard */

export type Grade = 'CORRECT' | 'WRONG' | 'TIE' | 'NO_PICK'

export interface PredictionRow {
  game_id: number
  season: number
  season_type: SeasonType
  week_seq: number
  start_at: string | null
  home_team_id: number
  home_school: string
  away_team_id: number
  away_school: string
  pred_home_points: number
  pred_away_points: number
  pick_team_id: number | null
  pick_school: string | null
  pick_win_prob: number | null
  freeze_kind: FreezeKind
  freeze_label: string
  is_rescheduled: boolean
  home_points: number | null
  away_points: number | null
  grade: Grade | null
}

export interface PredictionsResponse {
  point: ResolvedPoint
  notice: Notice | null
  this_week: PredictionRow[]
  last_week: PredictionRow[]
  has_predictions: boolean
  has_grades: boolean
}

export interface ScorecardRow {
  population: Population
  freeze_kind: FreezeKind
  is_headline: boolean
  games_graded: number
  record_label: string
  pick_accuracy: number
  brier: number
  mae_margin: number
  line_correct: number | null
  line_wrong: number | null
  line_brier: number | null
  model_brier_on_line_games: number | null
}

export interface CalibrationBucket {
  bucket: number
  games_graded: number
  mean_pick_win_prob: number
  pick_win_share: number
}

export interface ScorecardResponse {
  point: ResolvedPoint
  notice: Notice | null
  population: Population
  week: ScorecardRow[]
  season: ScorecardRow[]
  calibration: CalibrationBucket[]
}

/* upsets */

export interface UpsetRow {
  game_id: number
  season: number
  season_type: SeasonType
  week: number
  week_seq: number
  winner_team_id: number
  winner_school: string
  loser_team_id: number
  loser_school: string
  winner_points: number
  loser_points: number
  winner_site: SiteKind
  prob_source: ProbSource
  winner_pregame_win_prob: number
  upset_magnitude: number
  week_rank: number | null
  season_rank: number | null
  all_time_rank: number | null
  all_time_tied: boolean
  all_time_from_season: number
  line_era_rank: number | null
  line_era_tied: boolean
  model_winner_win_prob: number | null
}

export interface UpsetsResponse {
  point: ResolvedPoint
  notice: Notice | null
  week: UpsetRow[]
  all_time: UpsetRow[]
  line_era: UpsetRow[]
  all_time_from_season: number
}

/* head to head */

export interface MatchupSeries {
  team_id: number
  opponent_team_id: number
  school: string
  opponent_school: string
  meetings: number
  wins: number
  losses: number
  ties: number
  points_for: number
  points_against: number
  first_season: number
  last_season: number
  last_game_id: number
  streak_kind: 'W' | 'L' | 'T'
  streak_length: number
  wins_home: number
  wins_away: number
  wins_neutral: number
  biggest_win_margin: number | null
  biggest_win_season: number | null
  biggest_loss_margin: number | null
  biggest_loss_season: number | null
}

export interface MatchupGame {
  game_id: number
  season: number
  season_type: SeasonType
  week: number
  start_at: string | null
  site: SiteKind
  team_points: number
  opponent_points: number
  game_result: 'W' | 'L' | 'T'
  prob_source: ProbSource | null
  team_pregame_win_prob: number | null
  upset_magnitude: number | null
}

export interface MatchupNext {
  game_id: number
  season: number
  week_seq: number
  start_at: string | null
  site: SiteKind
  prediction_kind: 'FROZEN' | 'CURRENT'
  team_win_prob: number
  pred_team_points: number
  pred_opponent_points: number
  stakes_run_id: number | null
  team_p_in_cfp_if_win: number | null
  team_p_in_cfp_if_lose: number | null
  opponent_p_in_cfp_if_win: number | null
  opponent_p_in_cfp_if_lose: number | null
}

export interface MatchupResponse {
  team: Team
  opponent: Team
  never_met: boolean
  series: MatchupSeries | null
  games: MatchupGame[]
  next: MatchupNext | null
  next_as_of_label: string
}

/* spending: plan labels, preview, quota */

export interface SpendPlan {
  source: SpendSourceKind
  live_calls: number
  fetch_mode: FetchMode | null
}

export interface CachedPreviewState {
  cached_at: string
  source: SpendSourceKind
  req_ms: number | null
}

export interface PreviewPlanResponse {
  team_id: number
  opponent_id: number
  game_id: number | null
  game_basis: 'next' | 'latest' | 'none'
  plan: SpendPlan
  cached: CachedPreviewState | null
  disabled_reason: string | null
  busy_reason: string | null
}

export interface PreviewRequest {
  team: number
  opponent: number
  max_calls: number
  force: boolean
}

export interface PreviewTeam {
  team_id: number | null
  school: string
  record: string | null
  sp_rating: number | null
  srs_rating: number | null
  elo: number | null
  fpi: number | null
  points: number | null
}

export interface PreviewOdds {
  provider: string | null
  spread: number | null
  over_under: number | null
  home_moneyline: number | null
  away_moneyline: number | null
}

export interface PreviewResponse {
  game_id: number
  cached: boolean
  fetched_at: string
  source: SpendSourceKind
  calls_spent: number
  req_source: string
  req_ms: number | null
  venue: string | null
  status: string | null
  home: PreviewTeam
  away: PreviewTeam
  odds: PreviewOdds | null
  fetch_log_ok: boolean
}

export interface QuotaResponse {
  mode: 'live' | 'replay'
  read_at: string | null
  age_seconds: number | null
  remaining_calls: number | null
  used_calls: number | null
  project_budget: number | null
  refresh_spendable: number | null
  backfill_spendable: number | null
}

/* timeline and follow */

export interface FollowKey {
  as_of_season: number
  as_of_week_seq: number
  publication_id: number | null
  whatif_run_id: number | null
  whatif_views_current: boolean
}

export interface FollowState {
  key: FollowKey
  hold: boolean
  stuck: boolean
}

export interface TimelineStep {
  step_no: number
  step_name: string
  step_status: StepStatus
  started_at: string | null
  ended_at: string | null
  seconds: number | null
  slices: number | null
  rows_loaded: number | null
  calls: number | null
  detail: string | null
}

export interface TimelineRun {
  run_id: number
  run_kind: string
  run_args: string | null
  run_status: RunStatus
  started_at: string | null
  steps: TimelineStep[]
}

export interface FetchRow {
  context: string
  table_name: string
  slice_key: string
  source: string
  status: number | null
  attempts: number | null
  ms: number | null
  rows: number | null
  requested_at: string | null
}

export interface TimelineResponse {
  run: TimelineRun | null
  fetches: FetchRow[]
  follow: FollowState
}

/* refresh */

export interface RefreshTarget {
  season: number
  week: number
  season_type: SeasonType
}

export interface RefreshEstimate {
  total_calls: number
  per_table: Record<string, number>
}

export interface WeekCompleteness {
  games: number
  completed: number
  not_kicked_off: number
  point_will_move: boolean
  message: string
}

export interface RunningRun {
  run_id: number
  run_kind: string
  started_at: string | null
}

export interface RefreshPlanResponse {
  target: RefreshTarget
  mode: FetchMode
  can_run: boolean
  reasons: string[]
  estimate: RefreshEstimate
  needs_confirmation: boolean
  plus_retries: boolean
  quota: QuotaResponse | null
  ledger: LedgerConfig
  running: RunningRun[]
  week: WeekCompleteness
  dev_blocks_live: boolean
}

export interface RefreshRunRequest {
  season: number
  week: number
  season_type: SeasonType
  max_calls: number
}

export interface RefreshRunResponse {
  action_id: string
}

export type ActionState = 'RUNNING' | 'DONE' | 'FAILED'

export interface PointRef {
  season: number
  week_seq: number
  label: string
}

export interface ActionResponse {
  action_id: string
  kind: 'refresh' | 'preview' | 'console'
  state: ActionState
  run_id: number | null
  started_at: string
  ended_at: string | null
  error: string | null
  calls_spent: number | null
  point_before: PointRef | null
  point_after: PointRef | null
  point_message: string | null
}

/* inspector and console */

export interface RequestLine {
  slice: string
  source: string
  status: number | null
  attempts: number | null
  ms: number | null
  rows: number | null
  requested_at: string | null
}

export interface QueryLogEntry {
  entry_id: string
  principal: Principal
  user: string
  page: string | null
  sql: string
  started_at: string
  ms: number
  row_count: number | null
  error: string | null
  requests: RequestLine[]
  calls_spent: number
}

export interface InspectorResponse {
  entries: QueryLogEntry[]
}

export interface PlanManifest {
  table: string
  fetch_mode: string | null
  read_through: boolean
  budget: number | null
  planned_requests: number
  slice_keys: string[]
  sources: string[]
}

export interface ExplainResponse {
  entry_id: string
  principal: Principal
  no_virtual_table: boolean
  message: string | null
  manifests: PlanManifest[]
  spend: SpendPlan
  refused: string | null
}

export type ConsoleQueryId = 'fan-out' | 'contradiction' | 'stored' | 'over-budget'

export interface ConsoleQuery {
  id: ConsoleQueryId
  title: string
  summary: string
  sql: string
}

export interface ConsoleResponse {
  principal: Principal
  user: string
  help: string
  queries: ConsoleQuery[]
}

export interface ConsoleRunRequest {
  max_calls: number
}

export interface ConsoleRunResponse {
  id: ConsoleQueryId
  action_id: string
  columns: string[]
  rows: unknown[][]
  row_count: number
  calls_spent: number
  source: SpendSourceKind
  requests: RequestLine[]
  fetch_log_ok: boolean
}

/* UI revision: map, team page, league tabs. Every response maps its panels to query-log entry ids (`queries`). */

export type WinProbSource = 'OURS' | 'CFBD'
export type GameStatus = 'FINAL' | 'UPCOMING'
export type PredictionKind = 'FROZEN' | 'CURRENT'

/** A team at its home stadium, with its record and AP rank at the point. Coordinates are null when unknown. */
export interface MapTeam {
  team_id: number
  school: string
  mascot: string | null
  abbreviation: string | null
  conference: string | null
  color: string | null
  alternate_color: string | null
  has_logo: boolean
  latitude: number | null
  longitude: number | null
  wins: number
  losses: number
  conf_wins: number
  conf_losses: number
  ap_rank: number | null
}

export interface TeamsMapResponse {
  point: ResolvedPoint
  notice: Notice | null
  conferences: string[]
  teams: MapTeam[]
  queries: Record<string, string>
}

export interface TeamHero {
  team_id: number
  school: string
  mascot: string | null
  abbreviation: string | null
  conference: string | null
  color: string | null
  alternate_color: string | null
  has_logo: boolean
  wins: number
  losses: number
  conf_wins: number
  conf_losses: number
  ap_rank: number | null
  cfp_rank: number | null
}

/** One game of the team's schedule. A game after the point is UPCOMING and carries the line and a probability. */
export interface TeamGame {
  game_id: number
  week: number
  season_type: SeasonType
  start_at: string | null
  status: GameStatus
  site: SiteKind
  opponent: Team
  opponent_rank: number | null
  team_points: number | null
  opponent_points: number | null
  result: 'W' | 'L' | 'T' | null
  /** The line from the team's side: negative when the team is favored. */
  spread: number | null
  win_prob: number | null
  win_prob_source: WinProbSource | null
}

export interface SpRating {
  season: number
  overall: number
  overall_rank: number
  offense: number
  offense_rank: number
  defense: number
  defense_rank: number
}

export interface PollPoint {
  week_seq: number
  label: string
  ap_rank: number | null
  cfp_rank: number | null
}

export interface TeamResponse {
  point: ResolvedPoint
  notice: Notice | null
  team: TeamHero
  games: TeamGame[]
  sp: SpRating[]
  polls: PollPoint[]
  queries: Record<string, string>
}

export interface OddsTrajectoryPoint {
  season: number
  week_seq: number
  label: string
  p_in_cfp: number
  p_bye: number
  p_champion: number
}

export interface OutlookOdds {
  available: boolean
  reason: string | null
  move: Move | null
  delta_p_in_cfp: number | null
  current: OddsTrajectoryPoint | null
  trajectory: OddsTrajectoryPoint[]
}

export interface OutlookGame {
  game_id: number
  week_seq: number
  start_at: string | null
  site: SiteKind
  opponent: Team
  opponent_rank: number | null
  team_win_prob: number
  pred_team_points: number
  pred_opponent_points: number
  spread: number | null
  prediction_kind: PredictionKind
}

export interface OutlookThisWeek {
  available: boolean
  reason: string | null
  game: OutlookGame | null
}

export interface OutlookWatch {
  available: boolean
  reason: string | null
  guide: GuideResponse | null
}

export interface OutlookRivalries {
  available: boolean
  reason: string | null
  series: MatchupSeries[]
}

export interface TeamOutlookResponse {
  point: ResolvedPoint
  run_id: number | null
  team_id: number
  odds: OutlookOdds
  this_week: OutlookThisWeek
  watch: OutlookWatch
  rivalries: OutlookRivalries
  queries: Record<string, string>
}

/** One game of the coming week. The backend orders the list: stakes first with a run, else ranked pairs, close lines, kickoff. */
export interface WeekGame {
  game_id: number
  week_seq: number
  start_at: string | null
  venue: string | null
  home: Team
  away: Team
  home_rank: number | null
  away_rank: number | null
  /** The line from the home side: negative when the home team is favored. */
  spread: number | null
  home_win_prob: number | null
  win_prob_source: WinProbSource | null
  /** The largest swing in any team's P(CFP) the game decides, with a published run. */
  stakes: number | null
  game_of_week: boolean
  upset_alert_team_id: number | null
}

export interface LeagueWeekResponse {
  point: ResolvedPoint
  notice: Notice | null
  week_seq: number | null
  run_id: number | null
  games: WeekGame[]
  queries: Record<string, string>
}

export interface TravelRow {
  team: Team
  km: number
  trips: number
  longest_trip_km: number
  longest_trip_to: string
}

export interface MoverRow {
  team: Team
  sp_now: number
  sp_before: number
  delta: number
}

export interface ConferenceRow {
  conference: string
  teams: number
  mean_sp: number
  nonconf_wins: number
  nonconf_losses: number
  ranked_teams: number
}

export interface StreakRow {
  team: Team
  length: number
  since_season: number
  since_week: number
}

export interface LeagueStatsResponse {
  point: ResolvedPoint
  notice: Notice | null
  road_warriors: TravelRow[]
  risers: MoverRow[]
  fallers: MoverRow[]
  conference_strength: ConferenceRow[]
  streaks: StreakRow[]
  queries: Record<string, string>
}
