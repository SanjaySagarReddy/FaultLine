export type Check = { name: string; passed: boolean }
export type Evaluation = { evaluator_version: string; checks: Check[]; metrics: Record<string, unknown>; outcome: 'pass' | 'fail'; explanation: string }
export type Fault = { tool: string; effect: string; attempt: number }
export type Scenario = { id: string; name: string; severity: string; task: string; version: string; content_hash: string; faults: Fault[] }
export type Agent = { id: string; name: string; configured: boolean; model?: string }
export type Run = { id: string; scenario_id: string; scenario_hash: string; tool_manifest_hash: string; agent_id: string; agent_config: Record<string, unknown>; fault_plan: Fault[]; limits: Record<string, number>; status: string; termination_reason: string | null; final_answer: string | null; created_at: string; evaluation: Evaluation | null }
export type TraceEvent = { seq: number; timestamp_utc: string; type: string; source: string; call_id: string | null; payload: Record<string, unknown> }
export type ScenarioRate = { scenario_id: string; passing: number; conclusive: number; inconclusive: number; sample_count: number; pass_rate: number | null }
export type AgentSuite = { agent_id: string; suite_score: number | null; scenario_count: number; completed_scenarios: number; passing: number; conclusive: number; inconclusive: number; sample_count: number; scenarios: ScenarioRate[] }
export type Suite = { scenario_set: string[]; agents: AgentSuite[]; history: { passing: number; conclusive: number; inconclusive: number; reliability_score: number | null } }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${response.status}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  scenarios: () => request<Scenario[]>('/scenarios'),
  agents: () => request<Agent[]>('/agents'),
  runs: () => request<Run[]>('/runs'),
  run: (id: string) => request<Run>(`/runs/${id}`),
  events: (id: string) => request<{ events: TraceEvent[]; next_after_seq: number }>(`/runs/${id}/events?limit=500`),
  suite: () => request<Suite>('/suite'),
  start: (scenario_id: string, agent_id: string, model?: string, limits?: Record<string, number>) => request<{ id: string; status: string }>('/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenario_id, agent_id, model: model || null, limits }) }),
  compare: (ids: string[]) => request<{ compatible: boolean; sample_count: number; runs: Run[] }>(`/comparisons?run_ids=${ids.join(',')}`)
}
