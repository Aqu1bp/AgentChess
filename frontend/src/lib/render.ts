import type { BoardState, Piece, Key, PieceNode, PiecePoolSlot, SquareNode, Arrow, SquareHighlight, BrushColor } from './types';
import { key2pos, posToTranslate, translate, createEl } from './util';

type PieceName = string;

function pieceNameOf(piece: Piece): PieceName {
  return `${piece.color} ${piece.role}`;
}

function sqClass(key: Key): string {
  return `sq-${key}`;
}

// ─── Pool initialization (called once) ───

export function initPiecePool(container: HTMLElement, count: number): PiecePoolSlot[] {
  const slots: PiecePoolSlot[] = [];
  for (let i = 0; i < count; i++) {
    const el = createEl('piece', 'pool-spare') as PieceNode;
    el.cgKey = '' as Key;  // Empty — prevent stale cgKey matching in drag.ts DOM walk
    el.cgPiece = '';
    container.appendChild(el);
    slots.push({ element: el, currentKey: null, currentPiece: null });
  }
  return slots;
}

export function initSquarePool(container: HTMLElement): SquareNode[] {
  const nodes: SquareNode[] = [];
  for (let i = 0; i < 64; i++) {
    const el = createEl('square', 'pool-spare') as SquareNode;
    el.cgKey = 'a1' as Key;
    container.appendChild(el);
    nodes.push(el);
  }
  return nodes;
}

// ─── Main render (pool-based, no DOM add/remove) ───

export function render(
  state: BoardState,
  container: HTMLElement,
  piecePool?: PiecePoolSlot[],
  squarePool?: SquareNode[],
): void {
  // Fallback to legacy render if pools not initialized yet
  if (!piecePool || !squarePool) {
    renderLegacy(state, container);
    return;
  }

  const pieces = state.pieces;
  const curDrag = state.draggable.current;

  // ─── Piece pool update ───
  // Build a map of what pieces should be where
  const desired = new Map<Key, PieceName>();
  for (const [key, piece] of pieces) {
    desired.set(key, pieceNameOf(piece));
  }

  // Track which desired keys are already satisfied by a pool slot
  const satisfiedKeys = new Set<Key>();
  // Track which pool slots are free (not matching any desired piece)
  const freeSlots: number[] = [];

  // Pass 1: Check existing assignments
  for (let i = 0; i < piecePool.length; i++) {
    const slot = piecePool[i];
    if (slot.currentKey === null) {
      freeSlots.push(i);
      continue;
    }

    // Animating piece — update slot tracking to destination but don't touch the element.
    // The CSS keyframe animation is driving its transform; className changes would break it.
    if (slot.element.classList.contains('animating')) {
      const destKey = (slot.element as any).cgKey as Key;
      const destPiece = desired.get(destKey);
      if (destPiece) {
        satisfiedKeys.add(destKey);
        slot.currentKey = destKey;
        slot.currentPiece = destPiece;
      }
      continue;
    }

    const desiredPiece = desired.get(slot.currentKey);
    if (desiredPiece && desiredPiece === slot.currentPiece) {
      satisfiedKeys.add(slot.currentKey);
      if (!slot.element.cgDragging) {
        // Verify visual matches tracking (animation cleanup only updates sq-XX,
        // so promotions leave stale piece class — e.g. 'white pawn' instead of 'white queen')
        if (slot.element.cgPiece !== desiredPiece) {
          const [color, role] = desiredPiece.split(' ');
          slot.element.className = `${color} ${role} ${sqClass(slot.currentKey)}`;
          slot.element.style.transform = '';
          slot.element.cgPiece = desiredPiece;
        } else {
          updatePieceSquareClass(slot.element, slot.currentKey);
        }
      }
    } else {
      // Slot no longer matches — free it
      slot.element.className = 'pool-spare';
      slot.element.style.transform = '';
      (slot.element as any).cgKey = '';  // Clear stale key so drag.ts won't match this spare
      slot.currentKey = null;
      slot.currentPiece = null;
      freeSlots.push(i);
    }
  }

  // Clear drag state on pieces no longer being dragged
  for (const slot of piecePool) {
    if (slot.element.cgDragging && (!curDrag || curDrag.orig !== slot.currentKey)) {
      slot.element.classList.remove('dragging');
      slot.element.cgDragging = false;
      if (slot.currentKey) {
        updatePieceSquareClass(slot.element, slot.currentKey);
      }
    }
  }

  // Pass 2: Assign free slots to unsatisfied desired pieces
  for (const [key, pieceName] of desired) {
    if (satisfiedKeys.has(key)) continue;

    const slotIndex = freeSlots.pop();
    if (slotIndex === undefined) break; // shouldn't happen with 34 slots

    const slot = piecePool[slotIndex];
    const [color, role] = pieceName.split(' ');
    slot.element.className = `${color} ${role} ${sqClass(key)}`;
    slot.element.style.transform = ''; // let CSS class handle position
    slot.element.cgKey = key;
    slot.element.cgPiece = pieceName;
    slot.currentKey = key;
    slot.currentPiece = pieceName;
  }

  // ─── Square pool update ───
  const squareClassMap = computeSquares(state);
  // Track which square pool nodes are in use
  let sqPoolIdx = 0;

  // First, hide all square pool nodes
  for (const node of squarePool) {
    node.className = 'pool-spare';
    node.style.transform = '';
  }

  // Then assign them to active squares
  for (const [key, highlightClass] of squareClassMap) {
    if (sqPoolIdx >= squarePool.length) break;
    const node = squarePool[sqPoolIdx++];
    node.className = `${highlightClass} ${sqClass(key)}`;
    node.style.transform = ''; // let CSS class handle position
    node.cgKey = key;
  }
}

function updatePieceSquareClass(el: PieceNode, key: Key): void {
  // Replace sq-XX class without losing piece color/role classes
  const classes = el.className.split(' ').filter(c => !c.startsWith('sq-'));
  classes.push(sqClass(key));
  el.className = classes.join(' ');
  el.style.transform = ''; // clear any inline transform so CSS class takes over
}

// Legacy render for initial frame before pools are ready
function renderLegacy(state: BoardState, container: HTMLElement): void {
  const asWhite = state.orientation === 'white';
  const bounds = container.getBoundingClientRect();
  const posToTranslateFn = posToTranslate(bounds);
  const pieces = state.pieces;

  // Simple: clear and recreate (only used on first frame)
  const existing = Array.from(container.children);
  for (const child of existing) {
    if (child.tagName === 'PIECE' || child.tagName === 'SQUARE') {
      container.removeChild(child);
    }
  }

  for (const [key, piece] of pieces) {
    const el = createEl('piece', `${piece.color} ${piece.role}`) as PieceNode;
    el.cgKey = key;
    el.cgPiece = pieceNameOf(piece);
    translate(el, posToTranslateFn(key2pos(key), asWhite));
    container.appendChild(el);
  }

  const squareClassMap = computeSquares(state);
  for (const [key, className] of squareClassMap) {
    const el = createEl('square', className) as SquareNode;
    el.cgKey = key;
    translate(el, posToTranslateFn(key2pos(key), asWhite));
    container.appendChild(el);
  }
}

function computeSquares(state: BoardState): Map<Key, string> {
  const squares = new Map<Key, string>();

  if (state.lastMove) {
    for (const key of state.lastMove) {
      squares.set(key, 'last-move');
    }
  }

  if (state.premovable?.current) {
    squares.set(state.premovable.current.orig, 'premove');
    squares.set(state.premovable.current.dest, 'premove');
  }

  if (state.selected) {
    squares.set(state.selected, 'selected');

    if (state.movable.showDests && state.movable.dests) {
      const dests = state.movable.dests.get(state.selected);
      if (dests) {
        for (const dest of dests) {
          const hasPiece = state.pieces.has(dest);
          squares.set(dest, hasPiece ? 'move-dest oc' : 'move-dest');
        }
      }
    }
  }

  if (state.check) {
    squares.set(state.check, 'check');
  }

  return squares;
}


export function updateBounds(container: HTMLElement): void {
  // Clear explicit dimensions so the element flows back to its CSS-defined size.
  // Without this, the hardcoded px values prevent the element from resizing
  // when the parent changes (e.g. viewport resize via CSS min()/vw/vh).
  container.style.width = '';
  container.style.height = '';

  const bounds = container.getBoundingClientRect();
  if (bounds.width === 0 || bounds.height === 0) return;

  const ratio = bounds.height / bounds.width;
  const width = Math.floor((bounds.width * window.devicePixelRatio) / 8) * 8 / window.devicePixelRatio;
  const height = width * ratio;

  container.style.width = width + 'px';
  container.style.height = height + 'px';
}

const brushColors: Record<BrushColor, string> = {
  green: 'rgba(74, 222, 128, 0.8)',
  red: 'rgba(239, 68, 68, 0.8)',
  yellow: 'rgba(250, 204, 21, 0.8)',
  blue: 'rgba(59, 130, 246, 0.8)',
};

const brushHighlightColors: Record<BrushColor, string> = {
  green: 'rgba(74, 222, 128, 0.4)',
  red: 'rgba(239, 68, 68, 0.4)',
  yellow: 'rgba(250, 204, 21, 0.4)',
  blue: 'rgba(59, 130, 246, 0.4)',
};

function createSvgElement(tag: string): SVGElement {
  return document.createElementNS('http://www.w3.org/2000/svg', tag);
}

function getOrCreateSvgContainer(container: HTMLElement): SVGSVGElement {
  let svg = container.querySelector('.cg-annotations') as SVGSVGElement | null;
  if (!svg) {
    svg = createSvgElement('svg') as SVGSVGElement;
    svg.setAttribute('class', 'cg-annotations');
    svg.setAttribute('viewBox', '0 0 100 100');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:3;';

    const defs = createSvgElement('defs');
    for (const [brush, color] of Object.entries(brushColors)) {
      const marker = createSvgElement('marker');
      marker.setAttribute('id', `arrowhead-${brush}`);
      marker.setAttribute('markerWidth', '4');
      marker.setAttribute('markerHeight', '4');
      marker.setAttribute('refX', '2.5');
      marker.setAttribute('refY', '2');
      marker.setAttribute('orient', 'auto');

      const polygon = createSvgElement('polygon');
      polygon.setAttribute('points', '0 0, 4 2, 0 4');
      polygon.setAttribute('fill', color);
      marker.appendChild(polygon);
      defs.appendChild(marker);
    }
    svg.appendChild(defs);
    container.appendChild(svg);
  }
  return svg;
}

function keyToSvgPos(key: Key, asWhite: boolean): [number, number] {
  const pos = key2pos(key);
  const x = ((asWhite ? pos[0] : 7 - pos[0]) + 0.5) * 12.5;
  const y = ((asWhite ? 7 - pos[1] : pos[1]) + 0.5) * 12.5;
  return [x, y];
}

function renderArrow(svg: SVGSVGElement, arrow: Arrow, asWhite: boolean): void {
  const [x1, y1] = keyToSvgPos(arrow.from, asWhite);
  const [x2, y2] = keyToSvgPos(arrow.to, asWhite);

  const dx = x2 - x1;
  const dy = y2 - y1;
  const length = Math.sqrt(dx * dx + dy * dy);

  const shortenStart = 2;
  const shortenEnd = 4;
  const startX = x1 + (dx / length) * shortenStart;
  const startY = y1 + (dy / length) * shortenStart;
  const endX = x2 - (dx / length) * shortenEnd;
  const endY = y2 - (dy / length) * shortenEnd;

  const line = createSvgElement('line');
  line.setAttribute('x1', String(startX));
  line.setAttribute('y1', String(startY));
  line.setAttribute('x2', String(endX));
  line.setAttribute('y2', String(endY));
  line.setAttribute('stroke', brushColors[arrow.brush]);
  line.setAttribute('stroke-width', '2.5');
  line.setAttribute('stroke-linecap', 'round');
  line.setAttribute('marker-end', `url(#arrowhead-${arrow.brush})`);
  line.setAttribute('class', `arrow arrow-${arrow.brush}`);

  svg.appendChild(line);
}

function renderSquareHighlight(svg: SVGSVGElement, highlight: SquareHighlight, asWhite: boolean): void {
  const pos = key2pos(highlight.key);
  const x = (asWhite ? pos[0] : 7 - pos[0]) * 12.5;
  const y = (asWhite ? 7 - pos[1] : pos[1]) * 12.5;

  const rect = createSvgElement('rect');
  rect.setAttribute('x', String(x));
  rect.setAttribute('y', String(y));
  rect.setAttribute('width', '12.5');
  rect.setAttribute('height', '12.5');
  rect.setAttribute('fill', brushHighlightColors[highlight.brush]);
  rect.setAttribute('class', `highlight highlight-${highlight.brush}`);

  svg.appendChild(rect);
}

export function renderAnnotations(
  state: BoardState,
  container: HTMLElement
): void {
  const svg = getOrCreateSvgContainer(container);
  const asWhite = state.orientation === 'white';

  const children = Array.from(svg.children);
  for (const child of children) {
    if (child.tagName !== 'defs') {
      svg.removeChild(child);
    }
  }

  if (!state.annotations) return;

  for (const highlight of state.annotations.highlights) {
    renderSquareHighlight(svg, highlight, asWhite);
  }

  for (const arrow of state.annotations.arrows) {
    renderArrow(svg, arrow, asWhite);
  }
}

export function clearAnnotations(container: HTMLElement): void {
  const svg = container.querySelector('.cg-annotations') as SVGSVGElement | null;
  if (svg) {
    const children = Array.from(svg.children);
    for (const child of children) {
      if (child.tagName !== 'defs') {
        svg.removeChild(child);
      }
    }
  }
}
