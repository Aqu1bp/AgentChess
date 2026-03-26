import type { BoardState, Key, PieceNode } from './types';
import type { Chess } from 'chess.js';
import { eventPosition, getKeyAtDomPos, translate, translateDrag, posToTranslate, key2pos, setVisible } from './util';
import * as board from './board';

type DragEvent = PointerEvent | MouseEvent | TouchEvent;

export function start(state: BoardState, e: DragEvent, container: HTMLElement): void {
  if ('button' in e && e.buttons !== undefined && e.buttons > 1) return;
  
  const pos = eventPosition(e);
  if (!pos) return;
  
  const bounds = container.getBoundingClientRect();
  const asWhite = state.orientation === 'white';
  const orig = getKeyAtDomPos(pos, asWhite, bounds);
  
  if (!orig) return;
  
  const piece = state.pieces.get(orig);
  if (!piece) return;

  if (!isDraggable(state, orig)) return;

  e.preventDefault();

  const pieceEl = getPieceElement(container, orig);
  if (!pieceEl) return;

  state.draggable.current = {
    orig,
    piece,
    pos,
    origPos: pos,
    started: false,
    element: pieceEl,
  };

  processDrag(state, container);
}

export function move(state: BoardState, e: DragEvent, _container: HTMLElement): void {
  const cur = state.draggable.current;
  if (!cur) return;
  
  const pos = eventPosition(e);
  if (!pos) return;
  
  cur.pos = pos;

  if (!cur.started && distance(cur.pos, cur.origPos) >= state.draggable.distance) {
    cur.started = true;
  }
}

export function end(state: BoardState, e: DragEvent, container: HTMLElement, chess: Chess): boolean {
  const cur = state.draggable.current;
  if (!cur) return false;

  e.preventDefault();

  let moveMade = false;
  let premoveSet = false;

  if (cur.started) {
    const bounds = container.getBoundingClientRect();
    const asWhite = state.orientation === 'white';
    const dest = getKeyAtDomPos(cur.pos, asWhite, bounds);

    if (dest && dest !== cur.orig) {
      const isPlayerTurn = state.turnColor === state.orientation;

      if (isPlayerTurn) {
        board.clearPremove(state);
        moveMade = board.makeMove(state, cur.orig, dest, chess);
      } else if (state.premovable?.enabled) {
        board.setPremove(state, cur.orig, dest);
        premoveSet = true;
      }
    }
  }

  cancel(state, container);

  return moveMade || premoveSet;
}

export function cancel(state: BoardState, container: HTMLElement): void {
  const cur = state.draggable.current;
  if (!cur) return;
  
  const pieceEl = cur.element as PieceNode;
  pieceEl.classList.remove('dragging');
  pieceEl.cgDragging = false;

  const bounds = container.getBoundingClientRect();
  const asWhite = state.orientation === 'white';
  const posToTranslateFn = posToTranslate(bounds);
  translate(cur.element, posToTranslateFn(key2pos(cur.orig), asWhite));

  const ghost = container.querySelector('.ghost') as HTMLElement | null;
  if (ghost) setVisible(ghost, false);

  const dragHover = container.querySelector('.drag-hover') as HTMLElement | null;
  if (dragHover) setVisible(dragHover, false);

  state.draggable.current = undefined;
}

function processDrag(state: BoardState, container: HTMLElement): void {
  // Cache DOM lookups and bounds at drag start — avoid per-frame reflows
  const cachedBounds = container.getBoundingClientRect();
  const cachedGhost = container.querySelector('.ghost') as HTMLElement | null;
  const cachedDragHover = container.querySelector('.drag-hover') as HTMLElement | null;
  let lastPos: [number, number] | null = null;

  function tick() {
    const cur = state.draggable.current;
    if (!cur) return; // drag ended — stop the loop

    // Skip frame if position hasn't changed
    if (lastPos && cur.pos[0] === lastPos[0] && cur.pos[1] === lastPos[1]) {
      requestAnimationFrame(tick);
      return;
    }
    lastPos = [cur.pos[0], cur.pos[1]];

    if (cur.started) {
      const pieceEl = cur.element as PieceNode;
      if (!pieceEl.cgDragging) {
        pieceEl.classList.add('dragging');
        pieceEl.cgDragging = true;
      }

      const squareSize = cachedBounds.width / 8;
      translateDrag(cur.element, [
        cur.pos[0] - cachedBounds.left - squareSize / 2,
        cur.pos[1] - cachedBounds.top - squareSize / 2,
      ]);

      if (cachedGhost) {
        cachedGhost.className = `ghost ${cur.piece.color} ${cur.piece.role}`;
        const asWhite = state.orientation === 'white';
        const posToTranslateFn = posToTranslate(cachedBounds);
        translate(cachedGhost, posToTranslateFn(key2pos(cur.orig), asWhite));
        setVisible(cachedGhost, true);
      }

      const asWhite = state.orientation === 'white';
      const newOver = getKeyAtDomPos(cur.pos, asWhite, cachedBounds);

      if (newOver !== cur.over) {
        cur.over = newOver;
        if (cachedDragHover) {
          if (newOver && newOver !== cur.orig) {
            const posToTranslateFn = posToTranslate(cachedBounds);
            translate(cachedDragHover, posToTranslateFn(key2pos(newOver), asWhite));
            setVisible(cachedDragHover, true);
          } else {
            setVisible(cachedDragHover, false);
          }
        }
      }
    }

    requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}

function isDraggable(state: BoardState, orig: Key): boolean {
  const piece = state.pieces.get(orig);
  if (!piece) return false;

  if (!state.draggable.enabled) return false;

  if (state.movable.free) return true;
  if (state.turnColor === piece.color) return true;
  if (state.premovable?.enabled && piece.color === state.orientation) return true;

  return false;
}

function getPieceElement(container: HTMLElement, key: Key): PieceNode | undefined {
  let el = container.firstChild as HTMLElement | null;
  while (el) {
    if (el.tagName === 'PIECE' && (el as PieceNode).cgKey === key && !el.classList.contains('pool-spare')) {
      return el as PieceNode;
    }
    el = el.nextSibling as HTMLElement | null;
  }
  return undefined;
}

function distance(a: [number, number], b: [number, number]): number {
  const dx = a[0] - b[0];
  const dy = a[1] - b[1];
  return Math.sqrt(dx * dx + dy * dy);
}