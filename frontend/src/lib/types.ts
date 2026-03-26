// Core chess types - similar to chessground/types.ts

export const colors = ['white', 'black'] as const;
export const roles = ['pawn', 'knight', 'bishop', 'rook', 'queen', 'king'] as const;
export const files = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'] as const;
export const ranks = ['1', '2', '3', '4', '5', '6', '7', '8'] as const;

export type Color = (typeof colors)[number];
export type Role = (typeof roles)[number];
export type File = (typeof files)[number];
export type Rank = (typeof ranks)[number];
export type Key = `${File}${Rank}`;
export type FEN = string;

// Numeric position [file: 0-7, rank: 0-7]
export type Pos = [number, number];

// Pixel coordinates [x, y]
export type NumberPair = [number, number];

export interface Piece {
  role: Role;
  color: Color;
}

export type Pieces = Map<Key, Piece>;

export interface MoveMetadata {
  captured?: Piece;
  promotion?: Role;
}

export interface Dests extends Map<Key, Key[]> {}

export interface Premove {
  orig: Key;
  dest: Key;
}

export interface BoardState {
  pieces: Pieces;
  orientation: Color;
  turnColor: Color;
  selected?: Key;
  lastMove?: [Key, Key];
  check?: Key;
  movable: {
    free: boolean;
    color: Color | 'both';
    dests?: Dests;
    showDests: boolean;
  };
  draggable: {
    enabled: boolean;
    distance: number;
    current?: DragCurrent;
  };
  animation: {
    enabled: boolean;
    duration: number;
  };
  premovable: {
    enabled: boolean;
    current?: Premove;
  };
  annotations?: Annotations;
}

export interface DragCurrent {
  orig: Key;
  piece: Piece;
  pos: NumberPair;
  origPos: NumberPair;
  started: boolean;
  element: HTMLElement;
  over?: Key;
}

export interface AnimationPlan {
  anims: Map<Key, AnimationVector>; // piece key -> animation
  fadings: Map<Key, Piece>; // fading pieces
}

// [dx, dy, current dx, current dy]
export type AnimationVector = [number, number, number, number];

export interface AnimationCurrent {
  start: number;
  duration: number;
  plan: AnimationPlan;
}

export interface Elements {
  board: HTMLElement;
  container: HTMLElement;
  ghost?: HTMLElement;
}

export interface Bounds {
  (): DOMRect;
  clear: () => void;
}

// DOM element types
export interface PieceNode extends HTMLElement {
  cgKey: Key;
  cgPiece: string; // 'white pawn'
  cgAnimating?: boolean;
  cgFading?: boolean;
  cgDragging?: boolean;
}

export interface SquareNode extends HTMLElement {
  cgKey: Key;
}

export type MouchEvent = MouseEvent | TouchEvent;

// Annotation types for analysis arrows and highlights
export type BrushColor = 'green' | 'red' | 'yellow' | 'blue';

export interface Arrow {
  from: Key;
  to: Key;
  brush: BrushColor;
}

export interface SquareHighlight {
  key: Key;
  brush: BrushColor;
}

export interface Annotations {
  arrows: Arrow[];
  highlights: SquareHighlight[];
}

// Fixed DOM pool slot for piece elements
export interface PiecePoolSlot {
  element: PieceNode;
  currentKey: Key | null;      // which square (null = spare/hidden)
  currentPiece: string | null; // 'white pawn' etc (null = spare)
}
