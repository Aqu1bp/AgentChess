import { useCallback, useRef } from 'react'
import { Chessboard } from '../components/Chessboard'
import { ThinkingPanel } from '../components/ThinkingPanel'
import { useAgentChess } from '../hooks/useAgentChess'

export function Game() {
  const {
    gameState,
    thoughts,
    connected,
    aiThinking,
    sendMove,
    newGame,
    boardRef,
  } = useAgentChess()

  // Only pass FEN to Chessboard on initial render — after that, the board
  // manages its own state via makeMove/setPosition through the ref.
  // This prevents double-update when gameState.fen changes after AI moves.
  const initialFenRef = useRef<string | undefined>(undefined)
  if (gameState && !initialFenRef.current) {
    initialFenRef.current = gameState.fen
  }

  const handleMove = useCallback(
    (from: string, to: string, san: string) => {
      sendMove(from, to, san)
    },
    [sendMove]
  )

  // Format move list as pairs
  const moveList = gameState?.move_history || []
  const movePairs: string[] = []
  for (let i = 0; i < moveList.length; i += 2) {
    const num = Math.floor(i / 2) + 1
    const white = moveList[i]
    const black = moveList[i + 1] || ''
    movePairs.push(`${num}. ${white}${black ? ` ${black}` : ''}`)
  }

  const statusText = () => {
    if (!gameState) return ''
    switch (gameState.status) {
      case 'checkmate':
        return `Checkmate! ${gameState.winner === 'white' ? 'You win!' : 'AI wins!'}`
      case 'stalemate':
        return 'Stalemate - Draw'
      case 'draw_fifty':
        return 'Draw by fifty-move rule'
      case 'draw_repetition':
        return 'Draw by repetition'
      case 'draw_insufficient':
        return 'Draw - insufficient material'
      default:
        return gameState.turn === 'white' ? 'Your turn' : 'AI is thinking...'
    }
  }

  return (
    <div className="min-h-screen bg-surface-900 text-white">
      {/* Header */}
      <header className="border-b border-surface-600 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold tracking-tight">AgentChess</h1>
          <span className="text-xs text-white/40">Multi-Agent LLM Chess</span>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <span
              className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`}
            />
            <span className="text-xs text-white/50">
              {connected ? 'Connected' : 'Disconnected'}
            </span>
          </div>
          <button
            onClick={newGame}
            className="px-3 py-1.5 text-xs font-medium bg-surface-700 hover:bg-surface-600 border border-surface-600 rounded transition-colors"
          >
            New Game
          </button>
        </div>
      </header>

      {/* Main content */}
      <div className="flex justify-center p-6 gap-6 max-w-[1200px] mx-auto">
        {/* Left: Board */}
        <div className="flex flex-col gap-3">
          {/* AI label */}
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-surface-600 border border-white/20" />
            <span className="text-sm text-white/60">AI (Black)</span>
            {aiThinking && (
              <span className="text-xs text-accent animate-pulse ml-auto">thinking...</span>
            )}
          </div>

          {/* Board */}
          <div className="w-[480px] h-[480px]">
            <Chessboard
              ref={boardRef}
              fen={initialFenRef.current}
              orientation="white"
              playerColor="white"
              onMove={handleMove}
            />
          </div>

          {/* Player label */}
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-white border border-white/40" />
            <span className="text-sm text-white/60">You (White)</span>
          </div>

          {/* Status */}
          <div className="text-center text-sm text-white/50 mt-1">
            {statusText()}
          </div>
        </div>

        {/* Right: Thinking panel + Move list */}
        <div className="flex flex-col gap-3 w-[400px] min-h-[560px]">
          <div className="flex-1">
            <ThinkingPanel thoughts={thoughts} aiThinking={aiThinking} />
          </div>

          {/* Move list */}
          <div className="bg-surface-800 rounded-lg border border-surface-600 px-4 py-3">
            <h3 className="text-xs font-semibold text-white/40 uppercase tracking-wide mb-2">
              Moves
            </h3>
            <div className="text-sm text-white/70 font-mono leading-relaxed min-h-[2rem] max-h-[6rem] overflow-y-auto">
              {movePairs.length > 0 ? movePairs.join('  ') : 'Game not started'}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
