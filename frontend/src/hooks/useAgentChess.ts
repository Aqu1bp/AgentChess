import { useCallback, useEffect, useRef, useState } from 'react'

const API = '/api'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface GameState {
  game_id: string
  fen: string
  ply: number
  turn: 'white' | 'black'
  legal_moves: string[]
  last_move: { from: string; to: string; san: string; uci: string } | null
  move_history: string[]
  status: string
  winner: string | null
}

export interface AgentThought {
  agent: 'proposer' | 'validation'
  content: string
  phase: 'proposing' | 'validating' | 'deciding'
  ply: number
}

export interface SSEMoveEvent {
  side: string
  san: string
  uci: string
  from: string
  to: string
  fen: string
  ply: number
  status: string
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAgentChess() {
  const [gameState, setGameState] = useState<GameState | null>(null)
  const [thoughts, setThoughts] = useState<AgentThought[]>([])
  const [connected, setConnected] = useState(false)
  const [aiThinking, setAiThinking] = useState(false)

  const boardRef = useRef<any>(null)
  const previousFenRef = useRef<string>('')

  // Fetch initial game state
  const fetchGame = useCallback(async () => {
    try {
      const res = await fetch(`${API}/game`)
      if (res.ok) {
        const data: GameState = await res.json()
        setGameState(data)
        setAiThinking(data.turn === 'black' && data.status === 'playing')
        return data
      }
    } catch {
      // Server not running
    }
    return null
  }, [])

  // Send human move
  const sendMove = useCallback(async (from: string, to: string, san: string) => {
    if (!gameState) return

    // Save current FEN for rollback
    previousFenRef.current = gameState.fen

    // Determine promotion (auto-queen for now)
    const promotion = san.includes('=') ? 'q' : undefined

    try {
      const res = await fetch(`${API}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_sq: from, to_sq: to, promotion }),
      })

      if (res.ok) {
        // Success: update metadata from server, board already correct locally
        const data: GameState = await res.json()
        setGameState(data)
        setAiThinking(data.turn === 'black' && data.status === 'playing')
        // Clear thoughts for new AI turn
        setThoughts([])
      } else {
        // Failure: rollback the board
        if (boardRef.current && previousFenRef.current) {
          boardRef.current.setPosition(previousFenRef.current)
        }
      }
    } catch {
      // Network error: rollback
      if (boardRef.current && previousFenRef.current) {
        boardRef.current.setPosition(previousFenRef.current)
      }
    }
  }, [gameState])

  // New game
  const newGame = useCallback(async () => {
    try {
      const res = await fetch(`${API}/new-game`, { method: 'POST' })
      if (res.ok) {
        const data: GameState = await res.json()
        setGameState(data)
        setThoughts([])
        setAiThinking(false)
        if (boardRef.current) {
          boardRef.current.reset()
        }
      }
    } catch {
      // ignore
    }
  }, [])

  // SSE connection
  useEffect(() => {
    // Get last event ID from sessionStorage for page reload recovery
    const storedId = sessionStorage.getItem('agentchess_last_event_id')
    const url = storedId ? `${API}/stream?last_event_id=${storedId}` : `${API}/stream`

    const es = new EventSource(url)

    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)

    es.addEventListener('thought', (e: MessageEvent) => {
      const data: AgentThought = JSON.parse(e.data)
      setThoughts(prev => [...prev, data])
      setAiThinking(true)
      // Track event ID
      if (e.lastEventId) {
        sessionStorage.setItem('agentchess_last_event_id', e.lastEventId)
      }
    })

    es.addEventListener('move', (e: MessageEvent) => {
      const data: SSEMoveEvent = JSON.parse(e.data)
      if (e.lastEventId) {
        sessionStorage.setItem('agentchess_last_event_id', e.lastEventId)
      }

      if (data.side === 'black' && boardRef.current) {
        // AI moved — animate it on the board, skip onMove callback
        boardRef.current.makeMove(data.from, data.to, true)
      }

      // Update game state from SSE data directly — avoids double board update from fen prop change
      setGameState(prev => prev ? {
        ...prev,
        fen: data.fen,
        ply: data.ply,
        turn: data.ply % 2 === 0 ? 'white' : 'black',
        status: data.status,
        move_history: [...prev.move_history, data.san],
        last_move: { from: data.from, to: data.to, san: data.san, uci: data.uci },
      } : prev)
      setAiThinking(false)
    })

    es.addEventListener('game_state', (e: MessageEvent) => {
      if (e.lastEventId) {
        sessionStorage.setItem('agentchess_last_event_id', e.lastEventId)
      }
      fetchGame()
    })

    // Fetch initial state
    fetchGame()

    return () => {
      es.close()
      setConnected(false)
    }
  }, [fetchGame])

  return {
    gameState,
    thoughts,
    connected,
    aiThinking,
    sendMove,
    newGame,
    boardRef,
  }
}
