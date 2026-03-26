// FEN parsing - similar to chessground/fen.ts

import type { Pieces, Role, Color } from './types';
import { allKeys } from './util';

export const initial = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR';

const roles: { [letter: string]: Role } = {
  p: 'pawn',
  r: 'rook',
  n: 'knight',
  b: 'bishop',
  q: 'queen',
  k: 'king'
};

const letters = {
  pawn: 'p',
  rook: 'r',
  knight: 'n',
  bishop: 'b',
  queen: 'q',
  king: 'k'
};

export function read(fen: string): Pieces {
  const pieces: Pieces = new Map();
  
  if (fen === 'start') fen = initial;
  
  const rows = fen.split('/');
  
  for (let row = 7; row >= 0; row--) {
    const rowStr = rows[7 - row];
    let file = 0;
    
    for (const c of rowStr) {
      if (c === ' ') break;
      
      const nb = parseInt(c, 10);
      if (!isNaN(nb)) {
        file += nb;
      } else {
        const role = roles[c.toLowerCase()];
        if (role) {
          const color: Color = c === c.toLowerCase() ? 'black' : 'white';
          const key = allKeys[row * 8 + file];
          pieces.set(key, { role, color });
        }
        file++;
      }
    }
  }
  
  return pieces;
}

export function write(pieces: Pieces): string {
  let fen = '';
  let empty = 0;
  
  for (let rank = 7; rank >= 0; rank--) {
    for (let file = 0; file < 8; file++) {
      const key = allKeys[rank * 8 + file];
      const piece = pieces.get(key);
      
      if (!piece) {
        empty++;
      } else {
        if (empty > 0) {
          fen += empty;
          empty = 0;
        }
        const letter = letters[piece.role];
        fen += piece.color === 'white' ? letter.toUpperCase() : letter;
      }
    }
    
    if (empty > 0) {
      fen += empty;
      empty = 0;
    }
    
    if (rank !== 0) fen += '/';
  }
  
  return fen;
}
