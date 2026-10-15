export type Check = { name: string; passed: boolean }
export type Evaluation = { evaluator_version: string; checks: Check[]; metrics: Record<string, unknown>; outcome: 'pass' | 'fail'; explanation: string }
export type Fault = { tool: string; effect: 'transient_error' | 'timeout' | 'malformed_output' | 'delayed_response' | 'response_lost_after_commit'; stage: 'before_dispatch' | 'after_result' | 'before_delivery' | 'after_commit'; attempt: number; delay_ms: number }
export type Scenario = { id: string; name: string; severity: string; task: string; version: string; content_hash: string; tool_allowlist: string[]; faults: Fault[] }
export type Agent = { id: string; name: string; configured: boolean; model: string | null; implementation_version: string; prompt_version: string; provider_max_retries: number }
export type Run = { id: string; scenario_id: string; scenario_hash: string; tool_manifest_hash: string; agent_id: string; agent_config: Record<string, unknown>; fault_plan: Fault[]; fault_plan_source: string; limits: Record<string, number>; status: string; termination_reason: string | null; final_answer: string | null; created_at: string; evaluation: Evaluation | null }
export type TraceEvent = { seq: number; timestamp_utc: string; type: string; source: string; call_id: string | null; payload: Record<string, unknown> }
export type ScenarioRate = { scenario_id: string; passing: number; conclusive: number; inconclusive: number; sample_count: number; pass_rate: number | null }
export type SuiteCohort = { cohort_id: string; agent_id: string; agent_config: { id: string; implementation_version: string; prompt_version: string; model: string | null; provider_timeout_seconds: number; provider_max_retries: number }; limits: Record<string, number>; suite_score: number | null; scenario_count: number; completed_scenarios: number; passing: number; conclusive: number; inconclusive: number; sample_count: number; scenarios: ScenarioRate[] }
export type Suite = { scenario_set: string[]; scenario_conditions: Record<string, { scenario_hash: string; tool_manifest_hash: string; fault_plan_hash: string }>; suite_definition_hash: string; evaluator_version: string; cohorts: SuiteCohort[]; history: { passing: number; conclusive: number; inconclusive: number; reliability_score: number | null } }

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
  start: (scenario_id: string, agent_id: string, model?: string, limits?: Record<string, number>, fault_plan?: Fault[]) => request<{ id: string; status: string }>('/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenario_id, agent_id, model: model || null, limits, fault_plan }) }),
  compare: (ids: string[]) => request<{ compatible: boolean; sample_count: number; runs: Run[] }>(`/comparisons?run_ids=${ids.join(',')}`)
}
