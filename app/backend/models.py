"""Pydantic response and request models of the HTTP API (plan.md, Key Interfaces).

They mirror `app/frontend/src/api/types.ts`: keys are the serving views' columns in lower case.
Every fixture of `app/frontend/src/test/fixtures/manifest.json` must parse with the model of the
same name (`tests/unit/test_app_fixtures.py`), so the frontend and the backend cannot drift apart.
A change to the contract changes `types.ts`, the fixtures and this file together.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Number = int | float


FetchMode = Literal["LIVE", "REPLAY"]
SeasonType = Literal["regular", "postseason"]
Population = Literal["FBS_INVOLVED", "FBS_VS_FBS"]
FreezeKind = Literal["IN_ADVANCE", "RECONSTRUCTED", "ALL"]
Move = Literal["UP", "DOWN", "SAME", "NEW"]
ProbSource = Literal["LINE", "ELO"]
SiteKind = Literal["HOME", "AWAY", "NEUTRAL"]
SpendSourceKind = Literal["LIVE", "REPLAY", "STORED", "NONE"]
RunStatus = Literal["RUNNING", "DONE", "FAILED", "ABANDONED"]
StepStatus = Literal["PENDING", "RUNNING", "DONE", "FAILED", "SKIPPED"]
Principal = Literal["app", "reader", "chat"]
ApiErrorCode = Literal[
    "bad_host",
    "invalid_argument",
    "condition_error",
    "forbidden",
    "operator_disabled",
    "console_disabled",
    "not_found",
    "max_calls_exceeded",
    "ledger_refused",
    "quota_refused",
    "live_busy",
    "live_disabled",
    "reconcile_pending",
    "refresh_locked",
    "refresh_running",
    "whatif_run_changed",
    "cfbd_failed",
    "database_unreachable",
    "busy",
]


class ApiErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ApiErrorCode
    message: str
    calls_charged: Number | None = None


class Notice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    reason: str | None


class ChatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    enabled: bool
    reason: str | None
    model: str | None


class LedgerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on: bool
    plan: str | None
    plan_cap: Number | None
    plan_room: Number | None
    month: str | None
    month_cap: Number | None
    month_room: Number | None


class LiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    reasons: list[str]


class ConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    target: str
    dev: bool
    fetch_mode: FetchMode | None
    refresh_target: RefreshTarget | None
    operator: Capability
    console: Capability
    ledger: LedgerConfig
    live: LiveConfig
    read_check_skipped: bool
    chat: ChatConfig


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool


class CurrentPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_season: Number
    as_of_week: Number | None
    as_of_season_type: SeasonType
    as_of_week_seq: Number
    next_week_seq: Number | None
    as_of_label: str
    publication_id: Number | None
    whatif_publication_id: Number | None
    whatif_run_id: Number | None
    whatif_as_of_week_seq: Number | None
    whatif_views_current: bool


class AsOfPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_season: Number
    as_of_week: Number | None
    as_of_season_type: SeasonType
    as_of_week_seq: Number
    next_week_seq: Number | None
    as_of_label: str
    is_current: bool
    is_after_current: bool
    has_publication: bool
    has_predictions: bool
    has_grades: bool


class ResolvedPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: Number
    week_seq: Number
    next_week_seq: Number | None
    label: str
    is_current: bool


class PointsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: CurrentPoint
    season: Number
    seasons: list[Number]
    points: list[AsOfPoint]
    notice: Notice | None


class Team(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: Number
    school: str
    conference: str | None
    abbreviation: str | None
    color: str | None
    alternate_color: str | None
    has_logo: bool


class TeamsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    teams: list[Team]


class OddsRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    odds_rank: Number
    team_id: Number
    school: str
    conference: str | None
    p_in_cfp: Number
    p_bye: Number
    p_quarterfinal: Number
    p_semifinal: Number
    p_final: Number
    p_champion: Number
    p_conf_champion: Number | None
    expected_wins: Number
    mean_seed: Number | None
    prev_as_of_label: str | None
    prev_p_in_cfp: Number | None
    delta_p_in_cfp: Number | None
    delta_p_champion: Number | None
    rank_change: Number | None
    move: Move


class RunBanner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Number
    publication_id: Number
    run_origin: str
    n_sims: Number
    seconds_total: Number
    sims_per_second: Number | None
    nodes: Number | None
    instances: Number | None
    published_at: str


class WhatIfAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    reason: Literal["run_at_other_point", "regenerating", "no_run"] | None
    message: str | None
    run_id: Number | None
    run_label: str | None


class OddsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: ResolvedPoint
    notice: Notice | None
    rows: list[OddsRow]
    run: RunBanner | None
    move_threshold: Number
    whatif: WhatIfAvailability


class WhatIfRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Number
    season: Number
    as_of_week_seq: Number
    as_of_label: str
    n_sims: Number


class WhatIfGame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    season: Number
    week_seq: Number
    is_next_week: bool
    start_at: str | None
    home_team_id: Number
    home_school: str
    away_team_id: Number
    away_school: str
    home_win_prob: Number


class WhatIfGamesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: WhatIfRun | None
    games: list[WhatIfGame]
    max_conditions: Number


ConditionWinner = Literal["home", "away"]


class WhatIfCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    winner: ConditionWinner


class WhatIfOddsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Number
    conditions: list[WhatIfCondition]


class WhatIfOddsRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: Number
    school: str
    conference: str | None
    p_in_cfp: Number
    p_champion: Number
    base_p_in_cfp: Number
    base_p_champion: Number
    delta_p_in_cfp: Number
    delta_p_champion: Number
    low_sample: bool


class WhatIfOddsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Number
    n_sims: Number
    effective_sample: Number
    low_sample: bool
    method: str
    rows: list[WhatIfOddsRow]


class GuideRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    season: Number
    week_seq: Number
    is_next_week: bool
    guide_rank: Number | None
    next_week_rank: Number | None
    home_team_id: Number
    home_school: str
    away_team_id: Number
    away_school: str
    root_for_team_id: Number | None
    root_for_school: str | None
    p_if_home_win: Number | None
    p_if_away_win: Number | None
    leverage: Number | None
    low_sample: bool


class GuideResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Number | None
    team_id: Number
    conditional: bool
    top: list[GuideRow]
    coming_week: list[GuideRow]
    message: str | None


class WhatIfGuideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Number
    team_id: Number
    conditions: list[WhatIfCondition]


Grade = Literal["CORRECT", "WRONG", "TIE", "NO_PICK"]


class PredictionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    season: Number
    season_type: SeasonType
    week_seq: Number
    start_at: str | None
    home_team_id: Number
    home_school: str
    away_team_id: Number
    away_school: str
    pred_home_points: Number
    pred_away_points: Number
    pick_team_id: Number | None
    pick_school: str | None
    pick_win_prob: Number | None
    freeze_kind: FreezeKind
    freeze_label: str
    is_rescheduled: bool
    home_points: Number | None
    away_points: Number | None
    grade: Grade | None


class PredictionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: ResolvedPoint
    notice: Notice | None
    this_week: list[PredictionRow]
    last_week: list[PredictionRow]
    has_predictions: bool
    has_grades: bool


class ScorecardRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    population: Population
    freeze_kind: FreezeKind
    is_headline: bool
    games_graded: Number
    record_label: str
    pick_accuracy: Number
    brier: Number
    mae_margin: Number
    line_correct: Number | None
    line_wrong: Number | None
    line_brier: Number | None
    model_brier_on_line_games: Number | None


class CalibrationBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: Number
    games_graded: Number
    mean_pick_win_prob: Number
    pick_win_share: Number


class ScorecardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: ResolvedPoint
    notice: Notice | None
    population: Population
    week: list[ScorecardRow]
    season: list[ScorecardRow]
    calibration: list[CalibrationBucket]


class UpsetRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    season: Number
    season_type: SeasonType
    week: Number
    week_seq: Number
    winner_team_id: Number
    winner_school: str
    loser_team_id: Number
    loser_school: str
    winner_points: Number
    loser_points: Number
    winner_site: SiteKind
    prob_source: ProbSource
    winner_pregame_win_prob: Number
    upset_magnitude: Number
    week_rank: Number | None
    season_rank: Number | None
    all_time_rank: Number | None
    all_time_tied: bool
    all_time_from_season: Number
    line_era_rank: Number | None
    line_era_tied: bool
    model_winner_win_prob: Number | None


class UpsetsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: ResolvedPoint
    notice: Notice | None
    week: list[UpsetRow]
    all_time: list[UpsetRow]
    line_era: list[UpsetRow]
    all_time_from_season: Number


class MatchupSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: Number
    opponent_team_id: Number
    school: str
    opponent_school: str
    meetings: Number
    wins: Number
    losses: Number
    ties: Number
    points_for: Number
    points_against: Number
    first_season: Number
    last_season: Number
    last_game_id: Number
    streak_kind: Literal["W", "L", "T"]
    streak_length: Number
    wins_home: Number
    wins_away: Number
    wins_neutral: Number
    biggest_win_margin: Number | None
    biggest_win_season: Number | None
    biggest_loss_margin: Number | None
    biggest_loss_season: Number | None


class MatchupGame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    season: Number
    season_type: SeasonType
    week: Number
    start_at: str | None
    site: SiteKind
    team_points: Number
    opponent_points: Number
    game_result: Literal["W", "L", "T"]
    prob_source: ProbSource | None
    team_pregame_win_prob: Number | None
    upset_magnitude: Number | None


class MatchupNext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    season: Number
    week_seq: Number
    start_at: str | None
    site: SiteKind
    prediction_kind: Literal["FROZEN", "CURRENT"] | None
    team_win_prob: Number | None
    pred_team_points: Number | None
    pred_opponent_points: Number | None
    stakes_run_id: Number | None
    team_p_in_cfp_if_win: Number | None
    team_p_in_cfp_if_lose: Number | None
    opponent_p_in_cfp_if_win: Number | None
    opponent_p_in_cfp_if_lose: Number | None


class MatchupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team: Team
    opponent: Team
    never_met: bool
    series: MatchupSeries | None
    games: list[MatchupGame]
    next: MatchupNext | None
    next_as_of_label: str


class SpendPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SpendSourceKind
    live_calls: Number
    fetch_mode: FetchMode | None


class CachedPreviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cached_at: str
    source: SpendSourceKind
    req_ms: Number | None


class PreviewPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: Number
    opponent_id: Number
    game_id: Number | None
    game_basis: Literal["next", "latest", "none"]
    plan: SpendPlan
    cached: CachedPreviewState | None
    disabled_reason: str | None
    busy_reason: str | None


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team: Number
    opponent: Number
    max_calls: Number
    force: bool


class PreviewTeam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: Number | None
    school: str
    record: str | None
    sp_rating: Number | None
    srs_rating: Number | None
    elo: Number | None
    fpi: Number | None
    points: Number | None


class PreviewOdds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None
    spread: Number | None
    over_under: Number | None
    home_moneyline: Number | None
    away_moneyline: Number | None


class PreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    cached: bool
    fetched_at: str
    source: SpendSourceKind
    calls_spent: Number
    req_source: str
    req_ms: Number | None
    venue: str | None
    status: str | None
    home: PreviewTeam
    away: PreviewTeam
    odds: PreviewOdds | None
    fetch_log_ok: bool


class QuotaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["live", "replay"]
    read_at: str | None
    age_seconds: Number | None
    remaining_calls: Number | None
    used_calls: Number | None
    project_budget: Number | None
    refresh_spendable: Number | None
    backfill_spendable: Number | None


class FollowKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of_season: Number
    as_of_week_seq: Number
    publication_id: Number | None
    whatif_run_id: Number | None
    whatif_views_current: bool


class FollowState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: FollowKey
    hold: bool
    stuck: bool


class TimelineStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_no: Number
    step_name: str
    step_status: StepStatus
    started_at: str | None
    ended_at: str | None
    seconds: Number | None
    slices: Number | None
    rows_loaded: Number | None
    calls: Number | None
    detail: str | None


class TimelineRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Number
    run_kind: str
    run_args: str | None
    run_status: RunStatus
    started_at: str | None
    steps: list[TimelineStep]


class FetchRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: str
    table_name: str
    slice_key: str
    source: str
    status: Number | None
    attempts: Number | None
    ms: Number | None
    rows: Number | None
    requested_at: str | None


class TimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: TimelineRun | None
    fetches: list[FetchRow]
    follow: FollowState


class RefreshTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: Number
    week: Number
    season_type: SeasonType


class RefreshEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_calls: Number
    per_table: dict[str, Number]


class WeekCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    games: Number
    completed: Number
    not_kicked_off: Number
    point_will_move: bool
    message: str


class RunningRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Number
    run_kind: str
    started_at: str | None


class RefreshPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: RefreshTarget
    mode: FetchMode
    can_run: bool
    reasons: list[str]
    estimate: RefreshEstimate
    needs_confirmation: bool
    plus_retries: bool
    quota: QuotaResponse | None
    ledger: LedgerConfig
    running: list[RunningRun]
    week: WeekCompleteness
    dev_blocks_live: bool


class RefreshRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: Number
    week: Number
    season_type: SeasonType
    max_calls: Number


class RefreshRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str


ActionState = Literal["RUNNING", "DONE", "FAILED"]


class PointRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: Number
    week_seq: Number
    label: str


class ActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    kind: Literal["refresh", "preview", "console"]
    state: ActionState
    run_id: Number | None
    started_at: str
    ended_at: str | None
    error: str | None
    calls_spent: Number | None
    point_before: PointRef | None
    point_after: PointRef | None
    point_message: str | None


class RequestLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slice: str
    source: str
    status: Number | None
    attempts: Number | None
    ms: Number | None
    rows: Number | None
    requested_at: str | None


class QueryLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    principal: Principal
    user: str
    page: str | None
    sql: str
    started_at: str
    ms: Number
    row_count: Number | None
    error: str | None
    requests: list[RequestLine]
    calls_spent: Number
    cached: bool = False


class InspectorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[QueryLogEntry]


class PlanManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    fetch_mode: str | None
    read_through: bool
    budget: Number | None
    planned_requests: Number
    slice_keys: list[str]
    sources: list[str]


class ExplainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    principal: Principal
    no_virtual_table: bool
    message: str | None
    manifests: list[PlanManifest]
    spend: SpendPlan
    refused: str | None


ConsoleQueryId = Literal["fan-out", "contradiction", "stored", "over-budget"]


class ConsoleQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ConsoleQueryId
    title: str
    summary: str
    sql: str


class ConsoleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: Principal
    user: str
    help: str
    queries: list[ConsoleQuery]


class ConsoleRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_calls: Number


class ConsoleRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ConsoleQueryId
    action_id: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: Number
    calls_spent: Number
    source: SpendSourceKind
    requests: list[RequestLine]
    fetch_log_ok: bool


WinProbSource = Literal["OURS", "CFBD"]
GameStatus = Literal["FINAL", "UPCOMING"]
PredictionKind = Literal["FROZEN", "CURRENT"]


class MapTeam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: Number
    school: str
    mascot: str | None
    abbreviation: str | None
    conference: str | None
    color: str | None
    alternate_color: str | None
    has_logo: bool
    latitude: Number | None
    longitude: Number | None
    wins: Number
    losses: Number
    conf_wins: Number
    conf_losses: Number
    ap_rank: Number | None


class TeamsMapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: ResolvedPoint
    notice: Notice | None
    conferences: list[str]
    teams: list[MapTeam]
    queries: dict[str, str]


class TeamHero(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: Number
    school: str
    mascot: str | None
    abbreviation: str | None
    conference: str | None
    color: str | None
    alternate_color: str | None
    has_logo: bool
    wins: Number
    losses: Number
    conf_wins: Number
    conf_losses: Number
    ap_rank: Number | None
    cfp_rank: Number | None


class TeamGame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    week: Number
    season_type: SeasonType
    start_at: str | None
    status: GameStatus
    site: SiteKind
    opponent: Team
    opponent_rank: Number | None
    team_points: Number | None
    opponent_points: Number | None
    result: Literal["W", "L", "T"] | None
    spread: Number | None
    win_prob: Number | None
    win_prob_source: WinProbSource | None


class SpRating(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: Number
    overall: Number
    overall_rank: Number
    offense: Number
    offense_rank: Number
    defense: Number
    defense_rank: Number


class PollPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_seq: Number
    label: str
    ap_rank: Number | None
    cfp_rank: Number | None


class TeamResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: ResolvedPoint
    notice: Notice | None
    team: TeamHero
    games: list[TeamGame]
    sp: list[SpRating]
    polls: list[PollPoint]
    queries: dict[str, str]


class OddsTrajectoryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: Number
    week_seq: Number
    label: str
    p_in_cfp: Number
    p_bye: Number
    p_champion: Number


class OutlookOdds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    reason: str | None
    move: Move | None
    delta_p_in_cfp: Number | None
    current: OddsTrajectoryPoint | None
    trajectory: list[OddsTrajectoryPoint]


class OutlookGame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    week_seq: Number
    start_at: str | None
    site: SiteKind
    opponent: Team
    opponent_rank: Number | None
    team_win_prob: Number
    pred_team_points: Number | None
    pred_opponent_points: Number | None
    spread: Number | None
    prediction_kind: PredictionKind


class OutlookThisWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    reason: str | None
    game: OutlookGame | None


class OutlookWatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    reason: str | None
    guide: GuideResponse | None


class OutlookRivalries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    reason: str | None
    series: list[MatchupSeries]


class TeamOutlookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: ResolvedPoint
    run_id: Number | None
    team_id: Number
    odds: OutlookOdds
    this_week: OutlookThisWeek
    watch: OutlookWatch
    rivalries: OutlookRivalries
    queries: dict[str, str]


class WeekGame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: Number
    week_seq: Number
    start_at: str | None
    venue: str | None
    home: Team
    away: Team
    home_rank: Number | None
    away_rank: Number | None
    spread: Number | None
    home_win_prob: Number | None
    win_prob_source: WinProbSource | None
    stakes: Number | None
    game_of_week: bool
    upset_alert_team_id: Number | None


class LeagueWeekResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: ResolvedPoint
    notice: Notice | None
    week_seq: Number | None
    run_id: Number | None
    games: list[WeekGame]
    queries: dict[str, str]


class TravelRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team: Team
    km: Number
    trips: Number
    longest_trip_km: Number
    longest_trip_to: str


class MoverRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team: Team
    sp_now: Number
    sp_before: Number
    delta: Number


class ConferenceRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conference: str
    teams: Number
    mean_sp: Number
    nonconf_wins: Number
    nonconf_losses: Number
    ranked_teams: Number


class StreakRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team: Team
    length: Number
    since_season: Number
    since_week: Number


class LeagueStatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point: ResolvedPoint
    notice: Notice | None
    road_warriors: list[TravelRow]
    risers: list[MoverRow]
    fallers: list[MoverRow]
    conference_strength: list[ConferenceRow]
    streaks: list[StreakRow]
    queries: dict[str, str]
