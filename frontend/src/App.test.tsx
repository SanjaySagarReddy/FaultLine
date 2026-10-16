import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App, { Scorecard, Trace } from './App'
import { AgentMetric, SuiteOverview, matchingCohort } from './SuiteOverview'
import { FaultControls } from './FaultControls'
import { api } from './api'
import type { Agent, Run, Scenario, Suite, SuiteCohort, TraceEvent } from './api'

const run = { id: 'run-123', scenario_id: 'normal', scenario_hash: 'abc', tool_manifest_hash: 'def', agent_id: 'baseline', agent_config: {}, fault_plan: [], fault_plan_source: 'scenario_default', limits: {}, status: 'finished', termination_reason: 'agent_final', final_answer: 'Done', created_at: '2026-10-16T00:00:00Z', evaluation: { evaluator_version: '1.1.0', checks: [{ name: 'expected_payment_refunded_once', passed: true }], metrics: { business_correct: true, safety_clear: true, behavior_checks_pass: true, tool_call_count: 5 }, outcome: 'pass' as const, explanation: 'All checks passed.' } } satisfies Run

const limits = { max_turns: 12, max_tool_calls: 20, run_timeout_seconds: 120, provider_timeout_seconds: 25 }
const openai: Agent = { id: 'openai', name: 'OpenAI Responses', configured: true, model: 'model-a', implementation_version: 'openai-adapter-v1', prompt_version: 'openai-agent-v1', provider_max_retries: 0 }

function cohort(model: string, score: number): SuiteCohort {
  return {
    cohort_id: model, agent_id: 'openai',
    agent_config: { id: 'openai', model, implementation_version: 'openai-adapter-v1', prompt_version: 'openai-agent-v1', provider_timeout_seconds: 25, provider_max_retries: 0 },
    limits, suite_score: score, scenario_count: 1, completed_scenarios: 1, passing: score === 100 ? 1 : 0,
    conclusive: 1, inconclusive: 0, sample_count: 1,
    scenarios: [{ scenario_id: 'normal', passing: score === 100 ? 1 : 0, conclusive: 1, inconclusive: 0, sample_count: 1, pass_rate: score }],
  }
}

const suite: Suite = {
  scenario_set: ['normal'],
  scenario_conditions: { normal: { scenario_hash: 'hash', tool_manifest_hash: 'tools', fault_plan_hash: 'fault' } },
  suite_definition_hash: 'suite-hash', evaluator_version: '1.1.0',
  cohorts: [cohort('model-a', 100), cohort('model-b', 0)],
  history: { passing: 1, conclusive: 2, inconclusive: 0, reliability_score: 50 },
}

describe('run presentation', () => {
  it('renders persisted assertion results and metrics', () => { render(<Scorecard run={run} />); expect(screen.getByText('expected payment refunded once')).toBeInTheDocument(); expect(screen.getByText('Correct')).toBeInTheDocument(); expect(screen.getByText('Duplicate attempts')).toBeInTheDocument() })
  it('renders ordered event facts', () => { const events: TraceEvent[] = [{ seq: 2, timestamp_utc: '2026-10-16T00:00:00Z', type: 'state_changed', source: 'business_tool', call_id: 'call-1', payload: { payment_id: 'pay-duplicate' } }]; render(<Trace events={events} />); expect(screen.getByText('state changed')).toBeInTheDocument(); expect(screen.getByText('02')).toBeInTheDocument() })
  it('matches the selected model and limits instead of another model score', () => {
    expect(matchingCohort(suite, openai, 'model-a', limits)?.suite_score).toBe(100)
    expect(matchingCohort(suite, openai, 'model-b', limits)?.suite_score).toBe(0)
    expect(matchingCohort(suite, openai, 'model-c', limits)).toBeUndefined()
    expect(matchingCohort(suite, openai, 'model-a', { ...limits, max_turns: 1 })).toBeUndefined()
    render(<><AgentMetric agent={openai} selectedModel="model-c" limits={limits} result={matchingCohort(suite, openai, 'model-c', limits)} scenarioCount={1} faultPlanIsOverride={false} /><SuiteOverview suite={suite} /></>)
    expect(screen.getByLabelText('Selected configuration suite score')).toHaveTextContent('Incomplete')
    expect(screen.getByLabelText('Selected configuration suite score')).toHaveTextContent('model-c')
    expect(screen.getByText(/Pooled history across all agents, configurations, and scenario versions/)).toBeInTheDocument()
    expect(screen.getAllByText(/passing \| n=1/)).toHaveLength(2)
  })
})

describe('run fault selection', () => {
  it('sends a disabled or supported custom plan and restores the scenario default', () => {
    const scenario: Scenario = { id: 'normal', name: 'Normal', severity: 'low', task: 'Refund', version: '1', content_hash: 'hash', tool_allowlist: ['lookup_customer', 'execute_refund'], faults: [] }
    const onChange = vi.fn()
    render(<FaultControls scenario={scenario} maxToolCalls={20} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('FAULT PLAN'), { target: { value: 'none' } })
    expect(onChange).toHaveBeenLastCalledWith([])
    fireEvent.change(screen.getByLabelText('FAULT PLAN'), { target: { value: 'custom' } })
    expect(onChange).toHaveBeenLastCalledWith([{ tool: 'lookup_customer', attempt: 1, effect: 'transient_error', stage: 'before_dispatch', delay_ms: 0 }])
    fireEvent.change(screen.getByLabelText('TOOL'), { target: { value: 'execute_refund' } })
    fireEvent.change(screen.getByLabelText('EFFECT'), { target: { value: 'response_lost_after_commit' } })
    expect(onChange).toHaveBeenLastCalledWith([{ tool: 'execute_refund', attempt: 1, effect: 'response_lost_after_commit', stage: 'after_commit', delay_ms: 0 }])
    fireEvent.change(screen.getByLabelText('FAULT PLAN'), { target: { value: 'default' } })
    expect(onChange).toHaveBeenLastCalledWith(undefined)
  })

  it('hides a populated canonical header score for every explicit plan, including an identical one', async () => {
    const defaultFault: Scenario['faults'][number] = {
      tool: 'lookup_customer', attempt: 1, effect: 'transient_error', stage: 'before_dispatch', delay_ms: 0,
    }
    const scenario: Scenario = { id: 'normal', name: 'Normal', severity: 'low', task: 'Refund', version: '1', content_hash: 'hash', tool_allowlist: ['lookup_customer'], faults: [defaultFault] }
    const agent: Agent = { id: 'baseline', name: 'Baseline', configured: true, model: null, implementation_version: 'baseline-agent-v1', prompt_version: 'baseline-agent-v1', provider_max_retries: 0 }
    const baselineCohort: SuiteCohort = {
      ...cohort('model-a', 100), agent_id: 'baseline',
      agent_config: { ...cohort('model-a', 100).agent_config, id: 'baseline', model: null,
        implementation_version: agent.implementation_version, prompt_version: agent.prompt_version },
    }
    const start = vi.spyOn(api, 'start').mockResolvedValue({ id: 'override', status: 'queued' })
    vi.spyOn(api, 'scenarios').mockResolvedValue([scenario])
    vi.spyOn(api, 'agents').mockResolvedValue([agent])
    vi.spyOn(api, 'config').mockResolvedValue({ public_demo: true, temporary_history: true })
    vi.spyOn(api, 'runs').mockResolvedValue([])
    vi.spyOn(api, 'suite').mockResolvedValue({ ...suite, cohorts: [baselineCohort] })
    vi.spyOn(api, 'run').mockResolvedValue({ ...run, id: 'override', fault_plan: [defaultFault], fault_plan_source: 'run_override' })
    vi.spyOn(api, 'events').mockResolvedValue({ events: [], next_after_seq: 0 })
    const view = render(<App />)
    try {
      const header = await screen.findByLabelText('Selected configuration suite score')
      await waitFor(() => expect(within(header).getByText('100%')).toBeInTheDocument())
      expect(screen.getByLabelText('Configuration suite results')).toHaveTextContent('100%')
      expect(screen.getByText(/Run history resets when this service sleeps or restarts/)).toBeInTheDocument()
      expect(screen.getByText(/Scenario default: transient error/)).toBeInTheDocument()

      fireEvent.change(screen.getByLabelText('FAULT PLAN'), { target: { value: 'none' } })
      expect(header).toHaveTextContent('Not in canonical suite')
      expect(header).not.toHaveTextContent('100%')
      expect(header).not.toHaveTextContent('Incomplete')
      expect(screen.getByText('Run override: no injected fault')).toBeInTheDocument()
      expect(screen.getByLabelText('Configuration suite results')).toHaveTextContent('100%')

      fireEvent.change(screen.getByLabelText('FAULT PLAN'), { target: { value: 'custom' } })
      expect(header).toHaveTextContent('Not in canonical suite')
      expect(screen.getByText(/Run override: transient error/)).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: /start run/i }))
      await waitFor(() => expect(start).toHaveBeenCalledWith('normal', 'baseline', undefined, limits, [defaultFault]))

      fireEvent.change(screen.getByLabelText('FAULT PLAN'), { target: { value: 'default' } })
      expect(within(header).getByText('100%')).toBeInTheDocument()
      expect(screen.getByText(/Scenario default: transient error/)).toBeInTheDocument()
    } finally {
      view.unmount()
      vi.restoreAllMocks()
    }
  })
})
