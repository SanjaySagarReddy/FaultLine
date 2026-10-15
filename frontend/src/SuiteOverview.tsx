import type { Agent, Suite, SuiteCohort } from './api'

export function matchingCohort(
  suite: Suite | null,
  agent: Agent | undefined,
  selectedModel: string,
  limits: Record<string, number>,
): SuiteCohort | undefined {
  if (!suite || !agent) return undefined
  const model = agent.id === 'openai' ? (selectedModel || agent.model) : null
  return suite.cohorts.find(cohort =>
    cohort.agent_id === agent.id &&
    cohort.agent_config.id === agent.id &&
    cohort.agent_config.implementation_version === agent.implementation_version &&
    cohort.agent_config.prompt_version === agent.prompt_version &&
    cohort.agent_config.model === model &&
    cohort.agent_config.provider_max_retries === agent.provider_max_retries &&
    cohort.agent_config.provider_timeout_seconds === limits.provider_timeout_seconds &&
    Object.keys(cohort.limits).length === Object.keys(limits).length &&
    Object.entries(limits).every(([key, value]) => cohort.limits[key] === value),
  )
}

export function AgentMetric({ agent, selectedModel, limits, result, scenarioCount }: {
  agent?: Agent
  selectedModel: string
  limits: Record<string, number>
  result?: SuiteCohort
  scenarioCount: number
}) {
  const model = agent?.id === 'openai' ? (selectedModel || agent.model || 'Unspecified model') : 'Deterministic'
  return <div className="top-metric" aria-label="Selected configuration suite score">
    <span>{agent?.id.toUpperCase() || 'AGENT'} SUITE RELIABILITY</span>
    <strong>{result?.suite_score == null ? 'Incomplete' : result.suite_score.toFixed(0) + '%'}</strong>
    <small>{model} | {limits.max_turns} turns, {limits.max_tool_calls} calls | {result?.completed_scenarios ?? 0}/{scenarioCount} scenarios</small>
    <small>{limits.run_timeout_seconds}s run, {limits.provider_timeout_seconds}s provider | {agent?.implementation_version || 'Loading adapter'} | {agent?.prompt_version || 'Loading prompt'}</small>
  </div>
}

export function SuiteOverview({ suite }: { suite: Suite | null }) {
  if (!suite) return null
  return <section className="panel suite-overview" aria-label="Configuration suite results">
    <div className="panel-heading"><div><p className="eyebrow">CANONICAL SCENARIO SET</p><h2>Configuration suite results</h2></div><span className="count">{suite.scenario_set.length} scenarios</span></div>
    <p className="muted">A score needs one conclusive run in every current scenario. It averages scenario pass rates for one adapter, model, prompt, limits, and each scenario's canonical fault plan.</p>
    <div className="suite-agents">{suite.cohorts.length ? suite.cohorts.map(cohort => <div className="suite-agent" key={cohort.cohort_id}>
      <div className="suite-agent-heading"><strong>{cohort.agent_id} / {cohort.agent_config.model || 'deterministic'}</strong><span>{cohort.suite_score == null ? 'Incomplete' : cohort.suite_score.toFixed(0) + '%'}</span><small>{cohort.completed_scenarios}/{cohort.scenario_count} scenarios | {cohort.sample_count} samples</small></div>
      <div className="suite-config">{cohort.agent_config.implementation_version} | {cohort.agent_config.prompt_version}<br />Limits: {cohort.limits.max_turns} turns, {cohort.limits.max_tool_calls} calls, {cohort.limits.run_timeout_seconds}s run, {cohort.limits.provider_timeout_seconds}s provider</div>
      <div className="suite-scenario-list">{cohort.scenarios.map(scenario => <div className="suite-scenario" key={scenario.scenario_id}><span>{scenario.scenario_id.replaceAll('_', ' ')}</span><b>{scenario.pass_rate == null ? '—' : scenario.pass_rate.toFixed(0) + '%'}</b><small>{scenario.passing}/{scenario.conclusive} passing | n={scenario.sample_count}</small></div>)}</div>
    </div>) : <p className="muted">No canonical configuration cohort has finished yet.</p>}</div>
    <p className="muted pooled-history">Pooled history across all agents, configurations, and scenario versions: {suite.history.passing}/{suite.history.conclusive} conclusive runs passing{suite.history.reliability_score == null ? '' : ' (' + suite.history.reliability_score.toFixed(0) + '%)'}; {suite.history.inconclusive} inconclusive.</p>
  </section>
}
