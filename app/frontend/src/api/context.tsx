import { createContext, useContext, useMemo, type ReactNode } from 'react'

import type { ApiClient } from './client.ts'
import { createApi, type Api } from './endpoints.ts'

interface ApiServices {
  client: ApiClient
  api: Api
}

const ApiContext = createContext<ApiServices | null>(null)

/** Gives the components one API client (and its typed endpoints), so a test can inject a fake `fetch`. */
export function ApiProvider({ client, children }: { client: ApiClient; children: ReactNode }) {
  const services = useMemo(() => ({ client, api: createApi(client) }), [client])
  return <ApiContext.Provider value={services}>{children}</ApiContext.Provider>
}

function useServices(): ApiServices {
  const services = useContext(ApiContext)
  if (services === null) throw new Error('useApi needs an ApiProvider')
  return services
}

/** The typed endpoints. Every read takes `{ signal }` as its last argument. */
export function useApi(): Api {
  return useServices().api
}

export function useApiClient(): ApiClient {
  return useServices().client
}
