import type { BoardState, Key, Piece, Dests } from './types';
import { Chess } from 'chess.js';

export function selectSquare(state: BoardState, key: Key, chess: Chess): void {
  const piece = state.pieces.get(key);
  const playerTurn = isPlayerTurn(state);

  if (state.selected === key) {
    state.selected = undefined;
    return;
  }

  if (state.selected) {
    const from = state.selected;

    if (playerTurn) {
      const canMove = state.movable.dests?.get(from)?.includes(key);
      if (canMove) {
        clearPremove(state);
        makeMove(state, from, key, chess);
        state.selected = undefined;
        return;
      }
    } else if (state.premovable?.enabled) {
      const selectedPiece = state.pieces.get(from);
      if (selectedPiece && selectedPiece.color === state.orientation) {
        setPremove(state, from, key);
        state.selected = undefined;
        return;
      }
    }
  }

  if (piece && canSelect(state, key)) {
    state.selected = key;

    if (!state.movable.free && playerTurn) {
      const moves = chess.moves({ square: key, verbose: true });
      const dests = moves.map(m => m.to as Key);

      if (!state.movable.dests) {
        state.movable.dests = new Map();
      }
      state.movable.dests.set(key, dests);
    }
  } else {
    state.selected = undefined;
  }
}

export function makeMove(state: BoardState, from: Key, to: Key, chess: Chess): boolean {
  const piece = state.pieces.get(from);
  if (!piece) return false;

  if (piece.color !== state.turnColor) return false;

  try {
    const move = chess.move({
      from: from,
      to: to,
      promotion: 'q'
    });

    if (move) {
      syncBoardFromChess(state, chess);
      state.lastMove = [from, to];
      state.turnColor = state.turnColor === 'white' ? 'black' : 'white';

      if (chess.inCheck()) {
        const kingSquare = findKing(state.pieces, state.turnColor);
        state.check = kingSquare;
      } else {
        state.check = undefined;
      }

      updateDests(state, chess);

      return true;
    }
  } catch {
    return false;
  }

  return false;
}

export function setPremove(state: BoardState, orig: Key, dest: Key): void {
  if (!state.premovable) return;
  state.premovable.current = { orig, dest };
}

export function clearPremove(state: BoardState): void {
  if (!state.premovable) return;
  state.premovable.current = undefined;
}

export function playPremove(state: BoardState, chess: Chess): boolean {
  if (!state.premovable?.current) return false;

  const { orig, dest } = state.premovable.current;
  clearPremove(state);

  const piece = state.pieces.get(orig);
  if (!piece) return false;
  if (piece.color !== state.turnColor) return false;

  try {
    const move = chess.move({
      from: orig,
      to: dest,
      promotion: 'q'
    });

    if (move) {
      syncBoardFromChess(state, chess);
      state.lastMove = [orig, dest];
      state.turnColor = state.turnColor === 'white' ? 'black' : 'white';

      if (chess.inCheck()) {
        const kingSquare = findKing(state.pieces, state.turnColor);
        state.check = kingSquare;
      } else {
        state.check = undefined;
      }

      updateDests(state, chess);
      return true;
    }
  } catch {
    return false;
  }

  return false;
}

export function updateDests(state: BoardState, chess: Chess): void {
  if (state.movable.free) return;
  
  const dests: Dests = new Map();
  const moves = chess.moves({ verbose: true });
  
  for (const move of moves) {
    const from = move.from as Key;
    const to = move.to as Key;
    
    if (!dests.has(from)) {
      dests.set(from, []);
    }
    dests.get(from)!.push(to);
  }
  
  state.movable.dests = dests;
}

export function syncBoardFromChess(state: BoardState, chess: Chess): void {
  const pieces = new Map();
  const board = chess.board();
  
  for (let rank = 0; rank < 8; rank++) {
    for (let file = 0; file < 8; file++) {
      const square = board[7 - rank][file];
      if (square) {
        const key = `${String.fromCharCode(97 + file)}${rank + 1}` as Key;
        pieces.set(key, {
          role: pieceTypeToRole(square.type),
          color: square.color === 'w' ? 'white' : 'black'
        });
      }
    }
  }
  
  state.pieces = pieces;
}

function normalizeFen(fen: string): string {
  const parts = fen.trim().split(/\s+/);
  if (parts.length === 6) return fen;
  if (parts.length === 1) return `${parts[0]} w KQkq - 0 1`;
  return 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
}

export function setPieces(state: BoardState, fen: string, chess: Chess): void {
  fen = normalizeFen(fen);
  chess.load(fen);
  syncBoardFromChess(state, chess);
  state.turnColor = chess.turn() === 'w' ? 'white' : 'black';
  updateDests(state, chess);
}

function canSelect(state: BoardState, key: Key): boolean {
  const piece = state.pieces.get(key);
  if (!piece) return false;

  if (state.movable.free) return true;

  if (state.turnColor === piece.color) return true;

  if (state.premovable?.enabled) {
    const playerColor = state.orientation;
    return piece.color === playerColor;
  }

  return false;
}

function isPlayerTurn(state: BoardState): boolean {
  return state.turnColor === state.orientation;
}

export function findKing(pieces: Map<Key, Piece>, color: 'white' | 'black'): Key | undefined {
  for (const [key, piece] of pieces) {
    if (piece.role === 'king' && piece.color === color) {
      return key;
    }
  }
  return undefined;
}

function pieceTypeToRole(type: string): 'pawn' | 'knight' | 'bishop' | 'rook' | 'queen' | 'king' {
  const map: Record<string, any> = {
    'p': 'pawn',
    'n': 'knight',
    'b': 'bishop',
    'r': 'rook',
    'q': 'queen',
    'k': 'king'
  };
  return map[type] || 'pawn';
}