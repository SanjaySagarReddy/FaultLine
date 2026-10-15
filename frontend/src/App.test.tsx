import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Scorecard, Trace } from './App'
import { AgentMetric, SuiteOverview } from './SuiteOverview'
import type { Run, Suite, TraceEvent } from './api'

const run = { id: 'run-123', scenario_id: 'normal', scenario_hash: 'abc', tool_manifest_hash: 'def', agent_id: 'baseline', agent_config: {}, fault_plan: [], limits: {}, status: 'finished', termination_reason: 'agent_final', final_answer: 'Done', created_at: '2026-10-16T00:00:00Z', evaluation: { evaluator_version: '1.0.0', checks: [{ name: 'expected_payment_refunded_once', passed: true }], metrics: { business_correct: true, safety_clear: true, behavior_checks_pass: true, tool_call_count: 5 }, outcome: 'pass' as const, explanation: 'All checks passed.' } } satisfies Run

describe('run presentation', () => {
  it('renders persisted assertion results and metrics', () => { render(<Scorecard run={run} />); expect(screen.getByText('expected payment refunded once')).toBeInTheDocument(); expect(screen.getByText('Correct')).toBeInTheDocument(); expect(screen.getByText('Duplicate attempts')).toBeInTheDocument() })
  it('renders ordered event facts', () => { const events: TraceEvent[] = [{ seq: 2, timestamp_utc: '2026-10-16T00:00:00Z', type: 'state_changed', source: 'business_tool', call_id: 'call-1', payload: { payment_id: 'pay-duplicate' } }]; render(<Trace events={events} />); expect(screen.getByText('state changed')).toBeInTheDocument(); expect(screen.getByText('02')).toBeInTheDocument() })
  it('shows per-agent scenario rates and labels pooled history separately', () => {
    const suite: Suite = { scenario_set: ['normal'], history: { passing: 1, conclusive: 2, inconclusive: 0, reliability_score: 50 }, agents: [
      { agent_id: 'baseline', suite_score: 100, scenario_count: 1, completed_scenarios: 1, passing: 1, conclusive: 1, inconclusive: 0, sample_count: 1, scenarios: [{ scenario_id: 'normal', passing: 1, conclusive: 1, inconclusive: 0, sample_count: 1, pass_rate: 100 }] },
      { agent_id: 'openai', suite_score: 0, scenario_count: 1, completed_scenarios: 1, passing: 0, conclusive: 1, inconclusive: 0, sample_count: 1, scenarios: [{ scenario_id: 'normal', passing: 0, conclusive: 1, inconclusive: 0, sample_count: 1, pass_rate: 0 }] },
    ] }
    render(<><AgentMetric result={suite.agents[0]} /><SuiteOverview suite={suite} /></>)
    expect(screen.getByLabelText('Selected agent suite score')).toHaveTextContent('100%')
    expect(screen.getByText(/Pooled history across all agents and scenarios: 1\/2/)).toBeInTheDocument()
    expect(screen.getAllByText(/passing · n=1/)).toHaveLength(2)
  })
})
