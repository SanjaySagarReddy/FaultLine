import { useEffect, useMemo, useState } from 'react'
import { api, type Agent, type Run, type Scenario, type Suite, type TraceEvent } from './api'
import { AgentMetric, SuiteOverview } from './SuiteOverview'

function label(value: string) { return value.replaceAll('_', ' ') }
function short(id: string) { return id.slice(0, 8) }

export function Scorecard({ run }: { run: Run }) {
  const result = run.evaluation
  return <section className="panel scorecard" aria-label="Scorecard">
    <div className="panel-heading"><div><p className="eyebrow">EVALUATION</p><h2>Outcome scorecard</h2></div><span className={`status status-${result?.outcome || run.status}`}>{result?.outcome?.toUpperCase() || run.status.toUpperCase()}</span></div>
    {result ? <>
      <div className="score-grid">
        <div className="score-tile"><span>Business state</span><strong>{result.metrics.business_correct ? 'Correct' : 'Incorrect'}</strong></div>
        <div className="score-tile"><span>Safety</span><strong>{result.metrics.safety_clear ? 'Clear' : 'Flagged'}</strong></div>
        <div className="score-tile"><span>Recovery</span><strong>{result.metrics.behavior_checks_pass ? 'Passed' : 'Failed'}</strong></div>
        <div className="score-tile"><span>Tool calls</span><strong>{String(result.metrics.tool_call_count)}</strong></div>
      </div>
      <div className="checks"><h3>Assertions</h3>{result.checks.map(check => <div className="check" key={check.name}><span className={check.passed ? 'check-icon pass' : 'check-icon fail'}>{check.passed ? '✓' : '×'}</span><span>{label(check.name)}</span><b>{check.passed ? 'PASS' : 'FAIL'}</b></div>)}</div>
      <div className="metrics"><h3>Run metrics</h3><dl>
        <dt>Invalid calls</dt><dd>{String(result.metrics.invalid_call_count ?? 0)}</dd>
        <dt>Duplicate attempts</dt><dd>{String(result.metrics.duplicate_attempts ?? 0)}</dd>
        <dt>Duplicate effects</dt><dd>{String(result.metrics.successful_duplicate_effects ?? 0)}</dd>
        <dt>Latency</dt><dd>{result.metrics.latency_ms == null ? 'Unavailable' : `${result.metrics.latency_ms} ms`}</dd>
        <dt>Fault triggered</dt><dd>{result.metrics.fault_triggered ? 'Yes' : 'No'}</dd>
        <dt>Tokens (input / output)</dt><dd>{String((result.metrics.token_usage as Record<string, number> | undefined)?.input_tokens ?? 0)} / {String((result.metrics.token_usage as Record<string, number> | undefined)?.output_tokens ?? 0)}</dd>
        <dt>Cost estimate</dt><dd>{result.metrics.cost_estimate == null ? 'Unavailable' : String(result.metrics.cost_estimate)}</dd>
      </dl></div>
      <p className="muted">{result.explanation}</p>
    </> : <p className="muted">{run.status === 'inconclusive' ? `No fair determination: ${run.termination_reason}` : 'Waiting for the run to finish and state checks to complete.'}</p>}
  </section>
}

export function Trace({ events }: { events: TraceEvent[] }) {
  return <section className="panel trace" aria-label="Trace"><div className="panel-heading"><div><p className="eyebrow">OBSERVABILITY</p><h2>Execution trace</h2></div><span className="count">{events.length} events</span></div>
    <div className="trace-list">{events.length ? events.map(event => <details className={`event event-${event.type}`} key={event.seq} open={['fault_applied', 'state_changed', 'prohibited_action_attempt'].includes(event.type)}><summary><span className="seq">{String(event.seq).padStart(2, '0')}</span><span className="event-name">{label(event.type)}</span><span className="event-source">{event.source}</span><time>{new Date(event.timestamp_utc).toLocaleTimeString()}</time></summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>) : <p className="muted">Events appear as the agent works.</p>}</div>
  </section>
}

function Comparison({ runs }: { runs: Run[] }) {
  return <section className="panel" aria-label="Comparison"><div className="panel-heading"><div><p className="eyebrow">SIDE BY SIDE</p><h2>Compatible run comparison</h2></div><span className="count">{runs.length} samples</span></div>
    <div className="compare-grid">{runs.map(run => <div className="compare-card" key={run.id}><span className={`status status-${run.evaluation?.outcome}`}>{run.evaluation?.outcome?.toUpperCase()}</span><h3>{short(run.id)}</h3><p>{run.agent_id} · {run.scenario_id}</p><dl><dt>Tool calls</dt><dd>{String(run.evaluation?.metrics.tool_call_count)}</dd><dt>Invalid calls</dt><dd>{String(run.evaluation?.metrics.invalid_call_count)}</dd><dt>Fault triggered</dt><dd>{run.evaluation?.metrics.fault_triggered ? 'Yes' : 'No'}</dd></dl></div>)}</div>
  </section>
}

export default function App() {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [suite, setSuite] = useState<Suite | null>(null)
  const [selectedScenario, setSelectedScenario] = useState('normal')
  const [selectedAgent, setSelectedAgent] = useState('baseline')
  const [model, setModel] = useState('')
  const [limits, setLimits] = useState({ max_turns: 12, max_tool_calls: 20, run_timeout_seconds: 120, provider_timeout_seconds: 25 })
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedRun, setSelectedRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [comparison, setComparison] = useState<Run[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { Promise.all([api.scenarios(), api.agents()]).then(([s, a]) => { const order = ['normal', 'transient', 'response_loss', 'malformed', 'injection']; const ordered = [...s].sort((x, y) => order.indexOf(x.id) - order.indexOf(y.id)); setScenarios(ordered); setAgents(a); if (ordered[0]) setSelectedScenario(ordered[0].id) }).catch(e => setError(e.message)) }, [])
  useEffect(() => { const refresh = () => Promise.all([api.runs(), api.suite()]).then(([r, s]) => { setRuns(r); setSuite(s) }).catch(e => setError(e.message)); refresh(); const timer = setInterval(refresh, 2000); return () => clearInterval(timer) }, [])
  useEffect(() => { if (!selectedRunId) return; const refresh = () => Promise.all([api.run(selectedRunId), api.events(selectedRunId)]).then(([r, e]) => { setSelectedRun(r); setEvents(e.events) }).catch(e => setError(e.message)); refresh(); const timer = setInterval(refresh, 1500); return () => clearInterval(timer) }, [selectedRunId])
  const currentScenario = useMemo(() => scenarios.find(s => s.id === selectedScenario), [scenarios, selectedScenario])
  const selectedAgentSuite = suite?.agents.find(agent => agent.agent_id === selectedAgent)

  async function startRun() {
    setBusy(true); setError(''); setComparison(null)
    try { const created = await api.start(selectedScenario, selectedAgent, selectedAgent === 'openai' ? model : undefined, limits); setSelectedRunId(created.id); setCompareIds([]) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  function toggleCompare(id: string) { setCompareIds(old => old.includes(id) ? old.filter(x => x !== id) : [...old, id].slice(-8)); setComparison(null) }
  async function compare() { setError(''); try { const result = await api.compare(compareIds); setComparison(result.runs) } catch (e) { setError((e as Error).message) } }

  return <div className="app-shell"><aside className="sidebar"><div className="brand"><div className="brand-mark">F<span>/</span></div><div><strong>FAULTLINE</strong><small>AGENT RELIABILITY LAB</small></div></div><div className="nav-label">WORKSPACE</div><div className="nav-item active"><span>◫</span> Workbench</div><div className="nav-item"><span>≋</span> Run history</div><div className="sidebar-bottom"><div className="live-dot" /> LOCAL SIMULATOR <span className="version">v1.0</span></div></aside>
    <main><header className="topbar"><div><span className="breadcrumb">WORKSPACE <span>/</span> WORKBENCH</span><h1>Agent reliability, <em>measured.</em></h1><p>Test the action, inspect the trace, trust the state.</p></div><AgentMetric result={selectedAgentSuite} /></header>
      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError('')}>×</button></div>}
      <div className="content-grid"><section className="panel configure"><div className="panel-heading"><div><p className="eyebrow">01 / CONFIGURE</p><h2>New benchmark run</h2></div><span className="step-icon">↗</span></div>
        <label htmlFor="scenario">SCENARIO</label><select id="scenario" value={selectedScenario} onChange={e => setSelectedScenario(e.target.value)}>{scenarios.map(s => <option value={s.id} key={s.id}>{s.name}</option>)}</select>
        {currentScenario && <div className="scenario-detail"><div><span className={`severity severity-${currentScenario.severity}`}>{currentScenario.severity} severity</span><span className="scenario-version">v{currentScenario.version}</span></div><p>{currentScenario.task}</p><span className="fault-tag">{currentScenario.faults.length ? `${currentScenario.faults[0].effect.replaceAll('_', ' ')} · ${currentScenario.faults[0].tool}` : 'No injected fault'}</span></div>}
        <label htmlFor="agent">AGENT</label><select id="agent" value={selectedAgent} onChange={e => setSelectedAgent(e.target.value)}>{agents.map(a => <option value={a.id} key={a.id} disabled={!a.configured}>{a.name}{a.configured ? '' : ' · set OPENAI_API_KEY'}</option>)}</select>
        {selectedAgent === 'openai' && <><label htmlFor="model">MODEL</label><input id="model" value={model} onChange={e => setModel(e.target.value)} placeholder={agents.find(a => a.id === 'openai')?.model || 'Model ID'} /></>}
        <details className="limit-options"><summary>Run limits</summary><div className="limit-grid">{([['max_turns', 'Max turns'], ['max_tool_calls', 'Max tool calls'], ['run_timeout_seconds', 'Run timeout (seconds)'], ['provider_timeout_seconds', 'Provider timeout (seconds)']] as const).map(([key, title]) => <div key={key}><label htmlFor={key}>{title}</label><input id={key} type="number" min={1} value={limits[key]} onChange={e => setLimits(old => ({ ...old, [key]: Number(e.target.value) }))} /></div>)}</div></details>
        <button className="primary" onClick={startRun} disabled={busy || !currentScenario}>{busy ? 'Starting…' : 'Start run'} <span>→</span></button><p className="config-note">Each run gets isolated payment state and a versioned scenario snapshot.</p>
      </section>
      <section className="panel recent"><div className="panel-heading"><div><p className="eyebrow">02 / EXPLORE</p><h2>Recent runs</h2></div><span className="count">{runs.length} total</span></div>
        <div className="run-list">{runs.length ? runs.map(run => <div className={`run-row ${selectedRunId === run.id ? 'selected' : ''}`} key={run.id}><input type="checkbox" aria-label={`Compare ${short(run.id)}`} checked={compareIds.includes(run.id)} onChange={() => toggleCompare(run.id)} /><button className="run-select" onClick={() => { setSelectedRunId(run.id); setComparison(null) }}><span className="run-title">{scenarios.find(s => s.id === run.scenario_id)?.name || run.scenario_id}</span><span className="run-meta">{short(run.id)} · {run.agent_id} · {new Date(run.created_at).toLocaleString()}</span></button><span className={`status status-${run.evaluation?.outcome || run.status}`}>{run.evaluation?.outcome || run.status}</span></div>) : <div className="empty"><span>⌁</span><p>No runs yet. Start a scenario to inspect the trace.</p></div>}</div>
        <button className="secondary" disabled={compareIds.length < 2} onClick={compare}>Compare selected ({compareIds.length})</button>
      </section></div>
      <SuiteOverview suite={suite} />
      {comparison ? <Comparison runs={comparison} /> : selectedRun ? <div className="results"><div className="result-heading"><div><p className="eyebrow">03 / INSPECT</p><h2>Run {short(selectedRun.id)}</h2></div><span className="result-meta">{selectedRun.scenario_id} · {selectedRun.agent_id} · {selectedRun.termination_reason || selectedRun.status}</span></div><div className="result-grid"><Scorecard run={selectedRun} /><Trace events={events} /></div><div className="metadata"><span>Scenario hash: {selectedRun.scenario_hash.slice(0, 12)}</span><span>Tool hash: {selectedRun.tool_manifest_hash.slice(0, 12)}</span><span>Evaluator: {selectedRun.evaluation?.evaluator_version || 'pending'}</span></div><details className="repro"><summary>Run configuration and reproducibility</summary><pre>{JSON.stringify({ scenario_hash: selectedRun.scenario_hash, tool_manifest_hash: selectedRun.tool_manifest_hash, evaluator_version: selectedRun.evaluation?.evaluator_version, agent_config: selectedRun.agent_config, fault_plan: selectedRun.fault_plan, limits: selectedRun.limits }, null, 2)}</pre></details></div> : <section className="intro"><div className="intro-line"/><div><p className="eyebrow">WHY FAULTLINE</p><h2>Words are claims.<br /><em>State is evidence.</em></h2><p>Faultline checks the simulated business state after an agent runs, then shows every tool call, fault, and recovery step that led there.</p></div><div className="intro-number">01—05 <span>CURATED SCENARIOS</span></div></section>}
    </main></div>
}
