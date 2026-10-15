import type { AgentSuite, Suite } from './api'

export function AgentMetric({ result }: { result?: AgentSuite }) {
  return <div className="top-metric" aria-label="Selected agent suite score">
    <span>{result?.agent_id.toUpperCase() || 'AGENT'} SUITE RELIABILITY</span>
    <strong>{result?.suite_score == null ? '—' : `${result.suite_score.toFixed(0)}%`}</strong>
    <small>{result ? `${result.completed_scenarios} / ${result.scenario_count} scenarios · ${result.conclusive} conclusive samples` : 'No samples yet'}</small>
  </div>
}

export function SuiteOverview({ suite }: { suite: Suite | null }) {
  if (!suite) return null
  return <section className="panel suite-overview" aria-label="Agent suite results">
    <div className="panel-heading"><div><p className="eyebrow">SCENARIO SET</p><h2>Agent suite results</h2></div><span className="count">{suite.scenario_set.length} scenarios</span></div>
    <p className="muted">Each agent gets a score after at least one conclusive run in every current scenario. The score averages the scenario pass rates.</p>
    <div className="suite-agents">{suite.agents.map(agent => <div className="suite-agent" key={agent.agent_id}>
      <div className="suite-agent-heading"><strong>{agent.agent_id}</strong><span>{agent.suite_score == null ? 'Incomplete' : `${agent.suite_score.toFixed(0)}%`}</span><small>{agent.completed_scenarios}/{agent.scenario_count} scenarios · {agent.sample_count} samples</small></div>
      <div className="suite-scenario-list">{agent.scenarios.map(scenario => <div className="suite-scenario" key={scenario.scenario_id}><span>{scenario.scenario_id.replaceAll('_', ' ')}</span><b>{scenario.pass_rate == null ? '—' : `${scenario.pass_rate.toFixed(0)}%`}</b><small>{scenario.passing}/{scenario.conclusive} passing · n={scenario.sample_count}</small></div>)}</div>
    </div>)}</div>
    <p className="muted pooled-history">Pooled history across all agents and scenarios: {suite.history.passing}/{suite.history.conclusive} conclusive runs passing{suite.history.reliability_score == null ? '' : ` (${suite.history.reliability_score.toFixed(0)}%)`}; {suite.history.inconclusive} inconclusive.</p>
  </section>
}
