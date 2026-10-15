import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Scorecard, Trace } from './App'
import { AgentMetric, SuiteOverview, matchingCohort } from './SuiteOverview'
import type { Agent, Run, Suite, SuiteCohort, TraceEvent } from './api'

const run = { id: 'run-123', scenario_id: 'normal', scenario_hash: 'abc', tool_manifest_hash: 'def', agent_id: 'baseline', agent_config: {}, fault_plan: [], limits: {}, status: 'finished', termination_reason: 'agent_final', final_answer: 'Done', created_at: '2026-10-16T00:00:00Z', evaluation: { evaluator_version: '1.1.0', checks: [{ name: 'expected_payment_refunded_once', passed: true }], metrics: { business_correct: true, safety_clear: true, behavior_checks_pass: true, tool_call_count: 5 }, outcome: 'pass' as const, explanation: 'All checks passed.' } } satisfies Run

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
    render(<><AgentMetric agent={openai} selectedModel="model-c" limits={limits} result={matchingCohort(suite, openai, 'model-c', limits)} scenarioCount={1} /><SuiteOverview suite={suite} /></>)
    expect(screen.getByLabelText('Selected configuration suite score')).toHaveTextContent('Incomplete')
    expect(screen.getByLabelText('Selected configuration suite score')).toHaveTextContent('model-c')
    expect(screen.getByText(/Pooled history across all agents, configurations, and scenario versions/)).toBeInTheDocument()
    expect(screen.getAllByText(/passing \| n=1/)).toHaveLength(2)
  })
})
