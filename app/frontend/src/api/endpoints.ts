import type { ApiClient, Query, RequestOptions } from './client.ts'
import type {
  ActionResponse,
  ConfigResponse,
  ConsoleQueryId,
  ConsoleResponse,
  ConsoleRunRequest,
  ConsoleRunResponse,
  ExplainResponse,
  GuideResponse,
  HealthResponse,
  InspectorResponse,
  LeagueStatsResponse,
  LeagueWeekResponse,
  MatchupResponse,
  OddsResponse,
  Population,
  PointsResponse,
  PredictionsResponse,
  PreviewPlanResponse,
  PreviewRequest,
  PreviewResponse,
  QuotaResponse,
  RefreshPlanResponse,
  RefreshRunRequest,
  RefreshRunResponse,
  RefreshTarget,
  ScorecardResponse,
  TeamOutlookResponse,
  TeamResponse,
  TeamsMapResponse,
  TeamsResponse,
  TimelineResponse,
  UpsetsResponse,
  WhatIfGamesResponse,
  WhatIfGuideRequest,
  WhatIfOddsRequest,
  WhatIfOddsResponse,
} from './types.ts'

export interface PointQuery {
  season: number
  week: number
}

export interface Pair {
  team: number
  opponent: number
}

/** One typed method per endpoint of the HTTP API (plan.md). Reads take an optional abort signal. */
export function createApi(client: ApiClient) {
  const get = <T>(path: string, query?: Query, options?: RequestOptions) => client.get<T>(path, query, options)
  const post = <T>(path: string, body: unknown, options?: RequestOptions) => client.post<T>(path, body, options)
  const id = encodeURIComponent

  return {
    config: (options?: RequestOptions) => get<ConfigResponse>('/api/config', undefined, options),
    health: (options?: RequestOptions) => get<HealthResponse>('/api/health', undefined, options),
    points: (season?: number, options?: RequestOptions) =>
      get<PointsResponse>('/api/points', season === undefined ? undefined : { season }, options),
    teams: (options?: RequestOptions) => get<TeamsResponse>('/api/teams', undefined, options),
    teamsMap: (point: PointQuery, options?: RequestOptions) =>
      get<TeamsMapResponse>('/api/teams/map', { ...point }, options),
    team: (teamId: number, point: PointQuery, options?: RequestOptions) =>
      get<TeamResponse>(`/api/team/${teamId}`, { ...point }, options),
    teamOutlook: (teamId: number, point: PointQuery, options?: RequestOptions) =>
      get<TeamOutlookResponse>(`/api/team/${teamId}/outlook`, { ...point }, options),
    leagueWeek: (point: PointQuery, options?: RequestOptions) =>
      get<LeagueWeekResponse>('/api/league/week', { ...point }, options),
    leagueStats: (point: PointQuery, options?: RequestOptions) =>
      get<LeagueStatsResponse>('/api/league/stats', { ...point }, options),

    odds: (point: PointQuery, options?: RequestOptions) => get<OddsResponse>('/api/odds', { ...point }, options),
    whatifGames: (options?: RequestOptions) => get<WhatIfGamesResponse>('/api/whatif/games', undefined, options),
    whatifOdds: (request: WhatIfOddsRequest, options?: RequestOptions) =>
      post<WhatIfOddsResponse>('/api/whatif/odds', request, options),
    guide: (query: PointQuery & { team: number }, options?: RequestOptions) =>
      get<GuideResponse>('/api/guide', { ...query }, options),
    whatifGuide: (request: WhatIfGuideRequest, options?: RequestOptions) =>
      post<GuideResponse>('/api/whatif/guide', request, options),

    predictions: (point: PointQuery, options?: RequestOptions) =>
      get<PredictionsResponse>('/api/predictions', { ...point }, options),
    scorecard: (query: PointQuery & { population?: Population }, options?: RequestOptions) =>
      get<ScorecardResponse>('/api/scorecard', { ...query }, options),
    upsets: (point: PointQuery, options?: RequestOptions) => get<UpsetsResponse>('/api/upsets', { ...point }, options),
    matchup: (pair: Pair, options?: RequestOptions) => get<MatchupResponse>('/api/matchup', { ...pair }, options),

    previewPlan: (pair: Pair, options?: RequestOptions) =>
      get<PreviewPlanResponse>('/api/preview/plan', { ...pair }, options),
    preview: (request: PreviewRequest, options?: RequestOptions) => post<PreviewResponse>('/api/preview', request, options),
    quota: (query: { fresh?: boolean } = {}, options?: RequestOptions) =>
      get<QuotaResponse>('/api/quota', query.fresh === undefined ? undefined : { fresh: query.fresh }, options),

    timeline: (query: { run?: number } = {}, options?: RequestOptions) =>
      get<TimelineResponse>('/api/timeline', query.run === undefined ? undefined : { run: query.run }, options),
    refreshPlan: (target: RefreshTarget, options?: RequestOptions) =>
      post<RefreshPlanResponse>('/api/refresh/plan', target, options),
    refreshRun: (request: RefreshRunRequest, options?: RequestOptions) =>
      post<RefreshRunResponse>('/api/refresh/run', request, options),
    action: (actionId: string, options?: RequestOptions) =>
      get<ActionResponse>(`/api/actions/${id(actionId)}`, undefined, options),

    inspector: (options?: RequestOptions) => get<InspectorResponse>('/api/inspector', undefined, options),
    inspectorExplain: (entryId: string, options?: RequestOptions) =>
      post<ExplainResponse>(`/api/inspector/${id(entryId)}/explain`, {}, options),
    console: (options?: RequestOptions) => get<ConsoleResponse>('/api/console', undefined, options),
    consoleExplain: (queryId: ConsoleQueryId, options?: RequestOptions) =>
      post<ExplainResponse>(`/api/console/${id(queryId)}/explain`, {}, options),
    consoleRun: (queryId: ConsoleQueryId, request: ConsoleRunRequest, options?: RequestOptions) =>
      post<ConsoleRunResponse>(`/api/console/${id(queryId)}/run`, request, options),
  }
}

export type Api = ReturnType<typeof createApi>
