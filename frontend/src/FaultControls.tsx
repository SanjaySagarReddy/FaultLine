import { useState } from 'react'
import type { Fault, Scenario } from './api'

const effects: { value: Fault['effect']; label: string; stage: Fault['stage'] }[] = [
  { value: 'transient_error', label: 'Transient error', stage: 'before_dispatch' },
  { value: 'timeout', label: 'Timeout before dispatch', stage: 'before_dispatch' },
  { value: 'malformed_output', label: 'Malformed output', stage: 'after_result' },
  { value: 'delayed_response', label: 'Delayed response', stage: 'before_delivery' },
  { value: 'response_lost_after_commit', label: 'Response lost after commit', stage: 'after_commit' },
]

export function FaultControls({ scenario, maxToolCalls, onChange }: {
  scenario: Scenario
  maxToolCalls: number
  onChange: (plan: Fault[] | undefined) => void
}) {
  const [mode, setMode] = useState('default')
  const [tool, setTool] = useState(scenario.tool_allowlist[0])
  const [effect, setEffect] = useState<Fault['effect']>('transient_error')
  const [attempt, setAttempt] = useState(1)
  const [delayMs, setDelayMs] = useState(250)

  function emit(nextTool: string, nextEffect: Fault['effect'], nextAttempt: number, nextDelay: number) {
    const stage = effects.find(item => item.value === nextEffect)?.stage || 'before_dispatch'
    onChange([{ tool: nextTool, effect: nextEffect, stage, attempt: nextAttempt, delay_ms: nextEffect === 'delayed_response' ? nextDelay : 0 }])
  }

  return <div className="fault-controls">
    <label htmlFor="fault-mode">FAULT PLAN</label>
    <select id="fault-mode" value={mode} onChange={event => {
      const next = event.target.value
      setMode(next)
      if (next === 'default') onChange(undefined)
      else if (next === 'none') onChange([])
      else emit(tool, effect, attempt, delayMs)
    }}>
      <option value="default">Scenario default</option>
      <option value="none">No injected fault</option>
      <option value="custom">Custom single fault</option>
    </select>
    {mode === 'custom' && <div className="fault-grid">
      <div><label htmlFor="fault-tool">TOOL</label><select id="fault-tool" value={tool} onChange={event => {
        const nextTool = event.target.value
        const nextEffect = effect === 'response_lost_after_commit' && nextTool !== 'execute_refund' ? 'transient_error' : effect
        setTool(nextTool); setEffect(nextEffect); emit(nextTool, nextEffect, attempt, delayMs)
      }}>{scenario.tool_allowlist.map(name => <option key={name} value={name}>{name.replaceAll('_', ' ')}</option>)}</select></div>
      <div><label htmlFor="fault-effect">EFFECT</label><select id="fault-effect" value={effect} onChange={event => {
        const next = event.target.value as Fault['effect']
        setEffect(next); emit(tool, next, attempt, delayMs)
      }}>{effects.filter(item => item.value !== 'response_lost_after_commit' || tool === 'execute_refund').map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
      <div><label htmlFor="fault-attempt">TOOL ATTEMPT</label><input id="fault-attempt" type="number" min="1" max={maxToolCalls} value={attempt} onChange={event => {
        const next = Number(event.target.value)
        setAttempt(next); emit(tool, effect, next, delayMs)
      }} /></div>
      {effect === 'delayed_response' && <div><label htmlFor="fault-delay">DELAY (MS)</label><input id="fault-delay" type="number" min="0" max="2000" value={delayMs} onChange={event => {
        const next = Number(event.target.value)
        setDelayMs(next); emit(tool, effect, attempt, next)
      }} /></div>}
    </div>}
  </div>
}
