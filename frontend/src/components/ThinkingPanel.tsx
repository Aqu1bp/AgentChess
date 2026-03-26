import { useEffect, useRef } from 'react'
import type { AgentThought } from '../hooks/useAgentChess'

interface ThinkingPanelProps {
  thoughts: AgentThought[]
  aiThinking: boolean
}

const AGENT_CONFIG: Record<string, { label: string; color: string; border: string }> = {
  proposer: { label: 'Proposer', color: 'text-proposer', border: 'border-proposer/40' },
  validation: { label: 'Validator', color: 'text-validation', border: 'border-validation/40' },
}

const PHASE_LABELS: Record<string, string> = {
  proposing: 'Proposing',
  validating: 'Validating',
  deciding: 'Deciding',
}

export function ThinkingPanel({ thoughts, aiThinking }: ThinkingPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when new thoughts arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [thoughts])

  // Group thoughts by phase for visual separation
  const currentPhase = thoughts.length > 0 ? thoughts[thoughts.length - 1].phase : null

  return (
    <div className="flex flex-col h-full bg-surface-800 rounded-lg border border-surface-600 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-600">
        <h2 className="text-sm font-semibold text-white/90 tracking-wide uppercase">
          Thinking
        </h2>
        {aiThinking && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-white/50">
              {currentPhase ? PHASE_LABELS[currentPhase] || currentPhase : 'Waiting'}
            </span>
            <div className="flex gap-0.5">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:150ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse [animation-delay:300ms]" />
            </div>
          </div>
        )}
      </div>

      {/* Thoughts stream */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-2">
        {thoughts.length === 0 && !aiThinking && (
          <div className="text-white/30 text-sm text-center py-8">
            Play a move to see the AI think...
          </div>
        )}
        {thoughts.length === 0 && aiThinking && (
          <div className="text-white/40 text-sm text-center py-8">
            AI is building candidates and validating them...
          </div>
        )}
        {thoughts.map((thought, i) => {
          const config = AGENT_CONFIG[thought.agent] || {
            label: thought.agent,
            color: 'text-white/70',
            border: 'border-white/20',
          }
          // Show phase separator when phase changes
          const prevPhase = i > 0 ? thoughts[i - 1].phase : null
          const showPhaseSeparator = thought.phase !== prevPhase

          return (
            <div key={i}>
              {showPhaseSeparator && i > 0 && (
                <div className="flex items-center gap-2 py-2">
                  <div className="flex-1 h-px bg-surface-600" />
                  <span className="text-[10px] text-white/30 uppercase tracking-widest">
                    {PHASE_LABELS[thought.phase] || thought.phase}
                  </span>
                  <div className="flex-1 h-px bg-surface-600" />
                </div>
              )}
              <div className={`border-l-2 ${config.border} pl-3 py-1.5`}>
                <div className={`text-xs font-semibold ${config.color} mb-0.5`}>
                  {config.label}
                </div>
                <div className="text-sm text-white/75 leading-relaxed whitespace-pre-wrap font-mono">
                  {thought.content}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
