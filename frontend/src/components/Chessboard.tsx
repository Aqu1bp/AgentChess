import { useEffect, useRef, useCallback, useImperativeHandle, forwardRef } from 'react';
import { Chess } from 'chess.js';
import type { BoardState, Key, PieceNode, PiecePoolSlot, SquareNode, Annotations, Arrow, SquareHighlight, BrushColor } from '../lib/types';
import { render, initPiecePool, initSquarePool, updateBounds, renderAnnotations, clearAnnotations as clearAnnotationsSvg } from '../lib/render';
import * as drag from '../lib/drag';
import * as board from '../lib/board';
import { read as readFen, initial as initialFen } from '../lib/fen';
import { eventPosition, getKeyAtDomPos, key2pos } from '../lib/util';
import { playMoveSound, playCaptureSound, playCheckSound } from '../lib/sounds';
import './Chessboard.css';

export interface HoveredPiece {
  square: string;
  piece: string;
  color: 'white' | 'black';
}

interface ChessboardProps {
  fen?: string;
  orientation?: 'white' | 'black';
  playerColor?: 'white' | 'black' | 'both';  // 'both' = can move either side (analysis mode)
  onMove?: (from: string, to: string, san: string) => void;
  enablePremoves?: boolean;
  onPieceHover?: (hovered: HoveredPiece | null) => void;
  onSquareClick?: (square: string) => boolean;  // Return true to cancel default drag/select behavior
}

export interface ChessboardRef {
  makeMove: (from: string, to: string, skipCallback?: boolean) => Promise<boolean>;
  reset: () => void;
  getFen: () => string;
  setPosition: (fen: string, lastMove?: [string, string]) => void;
  setAnnotations: (annotations: Annotations) => void;
  clearAnnotations: () => void;
  playPremove: () => boolean;
  clearPremove: () => void;
  hasPremove: () => boolean;
}

export const Chessboard = forwardRef<ChessboardRef, ChessboardProps>(({
  fen = initialFen,
  orientation = 'white',
  playerColor = 'both',
  onMove,
  enablePremoves = false,
  onPieceHover,
  onSquareClick,
}, ref) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const boardRef = useRef<HTMLDivElement>(null);
  const chessRef = useRef<Chess>(new Chess());

  const drawingArrowRef = useRef<{ orig: Key; brush: BrushColor } | null>(null);
  const userArrowsRef = useRef<Arrow[]>([]);
  const userHighlightsRef = useRef<SquareHighlight[]>([]);

  const hoveredSquareRef = useRef<string | null>(null);
  const piecePoolRef = useRef<PiecePoolSlot[] | null>(null);
  const squarePoolRef = useRef<SquareNode[] | null>(null);

  const playerColorRef = useRef(playerColor);
  playerColorRef.current = playerColor;

  const stateRef = useRef<BoardState>({
    pieces: readFen(fen),
    orientation,
    turnColor: 'white',
    movable: {
      free: false,
      color: 'white',
      showDests: true,
    },
    draggable: {
      enabled: true,
      distance: 1,
    },
    animation: {
      enabled: true,
      duration: 200,
    },
    premovable: {
      enabled: enablePremoves,
    },
  });

  const redraw = useCallback(() => {
    if (!boardRef.current) return;
    render(stateRef.current, boardRef.current, piecePoolRef.current || undefined, squarePoolRef.current || undefined);
  }, []);

  const getPieceElement = useCallback((key: Key): PieceNode | null => {
    // Search pool first (fast path)
    if (piecePoolRef.current) {
      for (const slot of piecePoolRef.current) {
        if (slot.currentKey === key) return slot.element;
      }
      return null;
    }
    // Fallback: walk DOM
    if (!boardRef.current) return null;
    let el = boardRef.current.firstChild as HTMLElement | null;
    while (el) {
      if (el.tagName === 'PIECE' && (el as PieceNode).cgKey === key) {
        return el as PieceNode;
      }
      el = el.nextSibling as HTMLElement | null;
    }
    return null;
  }, []);

  const animatePiece = useCallback((from: Key, to: Key): Promise<void> => {
    return new Promise((resolve) => {
      if (!boardRef.current) {
        resolve();
        return;
      }

      const pieceEl = getPieceElement(from);
      if (!pieceEl) {
        resolve();
        return;
      }

      const asWhite = stateRef.current.orientation === 'white';

      // Compute percentage-based positions (matching sq-XY CSS grid)
      const fromPos = key2pos(from);
      const toPos = key2pos(to);
      const fromPctX = (asWhite ? fromPos[0] : 7 - fromPos[0]) * 100;
      const fromPctY = (asWhite ? 7 - fromPos[1] : fromPos[1]) * 100;
      const toPctX = (asWhite ? toPos[0] : 7 - toPos[0]) * 100;
      const toPctY = (asWhite ? 7 - toPos[1] : toPos[1]) * 100;

      // Set CSS variables for keyframe animation (no layout reflow needed)
      pieceEl.style.setProperty('--from-x', `${fromPctX}%`);
      pieceEl.style.setProperty('--from-y', `${fromPctY}%`);
      pieceEl.style.setProperty('--to-x', `${toPctX}%`);
      pieceEl.style.setProperty('--to-y', `${toPctY}%`);

      pieceEl.classList.add('animating');
      (pieceEl as any).cgKey = to;

      let cleaned = false;
      const cleanup = () => {
        if (cleaned) return;
        cleaned = true;
        pieceEl.classList.remove('animating');
        pieceEl.style.removeProperty('--from-x');
        pieceEl.style.removeProperty('--from-y');
        pieceEl.style.removeProperty('--to-x');
        pieceEl.style.removeProperty('--to-y');
        // Set final sq-XX class so CSS handles resting position
        // (needed when render doesn't run after animation, e.g. premoves)
        const classes = pieceEl.className.split(' ').filter(c => !c.startsWith('sq-'));
        classes.push(`sq-${to}`);
        pieceEl.className = classes.join(' ');
        pieceEl.style.transform = '';  // clear inline, let CSS class handle final position
        pieceEl.removeEventListener('animationend', onEnd);
        resolve();
      };

      const onEnd = () => cleanup();
      pieceEl.addEventListener('animationend', onEnd);

      // Fallback timeout (180ms animation + buffer)
      setTimeout(cleanup, 250);
    });
  }, [getPieceElement]);

  const makeMove = useCallback(async (from: string, to: string, skipCallback = false): Promise<boolean> => {
    const state = stateRef.current;
    const chess = chessRef.current;

    const piece = state.pieces.get(from as Key);
    if (!piece) return false;

    try {
      const move = chess.move({
        from: from,
        to: to,
        promotion: 'q'
      });

      if (!move) return false;

      if (chess.inCheck()) {
        playCheckSound();
      } else if (move.captured) {
        playCaptureSound();
      } else {
        playMoveSound();
      }

      await animatePiece(from as Key, to as Key);

      board.syncBoardFromChess(state, chess);
      state.lastMove = [from as Key, to as Key];
      state.turnColor = chess.turn() === 'w' ? 'white' : 'black';
      state.movable.color = state.turnColor;

      if (chess.inCheck()) {
        const kingSquare = board.findKing(state.pieces, state.turnColor);
        state.check = kingSquare;
      } else {
        state.check = undefined;
      }

      board.updateDests(state, chess);
      state.selected = undefined;
      redraw();

      if (onMove && !skipCallback) {
        onMove(from, to, move.san);
      }

      return true;
    } catch {
      return false;
    }
  }, [animatePiece, redraw, onMove]);

  const reset = useCallback(() => {
    const chess = chessRef.current;
    const state = stateRef.current;

    chess.reset();
    board.syncBoardFromChess(state, chess);
    state.turnColor = 'white';
    state.movable.color = 'white';
    state.lastMove = undefined;
    state.selected = undefined;
    state.check = undefined;
    board.updateDests(state, chess);
    redraw();
  }, [redraw]);

  const setPosition = useCallback((newFen: string, lastMove?: [string, string]) => {
    const chess = chessRef.current;
    const state = stateRef.current;

    chess.load(newFen);
    board.syncBoardFromChess(state, chess);
    state.turnColor = chess.turn() === 'w' ? 'white' : 'black';
    state.movable.color = state.turnColor;
    state.lastMove = lastMove ? [lastMove[0] as Key, lastMove[1] as Key] : undefined;
    state.selected = undefined;

    if (chess.inCheck()) {
      const kingSquare = board.findKing(state.pieces, state.turnColor);
      state.check = kingSquare;
    } else {
      state.check = undefined;
    }

    board.updateDests(state, chess);
    redraw();
  }, [redraw]);

  const setAnnotations = useCallback((annotations: Annotations) => {
    const state = stateRef.current;
    state.annotations = annotations;
    if (boardRef.current) {
      // Merge programmatic annotations with user-drawn arrows/highlights
      if (userArrowsRef.current.length > 0 || userHighlightsRef.current.length > 0) {
        const merged: Annotations = {
          arrows: [...annotations.arrows, ...userArrowsRef.current],
          highlights: [...annotations.highlights, ...userHighlightsRef.current],
        };
        const saved = state.annotations;
        state.annotations = merged;
        renderAnnotations(state, boardRef.current);
        state.annotations = saved;
      } else {
        renderAnnotations(state, boardRef.current);
      }
    }
  }, []);

  const clearAnnotations = useCallback(() => {
    const state = stateRef.current;
    state.annotations = undefined;
    if (boardRef.current) {
      // If user has drawn annotations, re-render those instead of clearing everything
      if (userArrowsRef.current.length > 0 || userHighlightsRef.current.length > 0) {
        renderUserAnnotationsRef.current();
      } else {
        clearAnnotationsSvg(boardRef.current);
      }
    }
  }, []);

  const getBrushFromEvent = useCallback((e: MouseEvent): BrushColor => {
    if (e.altKey) return 'red';
    if (e.ctrlKey || e.metaKey) return 'blue';
    if (e.shiftKey) return 'yellow';
    return 'green';
  }, []);

  const renderUserAnnotations = useCallback(() => {
    const state = stateRef.current;
    const programmaticArrows = state.annotations?.arrows || [];
    const programmaticHighlights = state.annotations?.highlights || [];

    const mergedAnnotations: Annotations = {
      arrows: [...programmaticArrows, ...userArrowsRef.current],
      highlights: [...programmaticHighlights, ...userHighlightsRef.current],
    };

    if (boardRef.current) {
      const originalAnnotations = state.annotations;
      state.annotations = mergedAnnotations;
      renderAnnotations(state, boardRef.current);
      state.annotations = originalAnnotations;
    }
  }, []);

  const clearUserAnnotations = useCallback(() => {
    userArrowsRef.current = [];
    userHighlightsRef.current = [];
    renderUserAnnotationsRef.current();
  }, [renderUserAnnotations]);

  useEffect(() => {
    if (!containerRef.current || !boardRef.current) return;

    const state = stateRef.current;
    const chess = chessRef.current;

    // Initialize fixed DOM pools (once)
    if (!piecePoolRef.current) {
      piecePoolRef.current = initPiecePool(boardRef.current, 34);
      squarePoolRef.current = initSquarePool(boardRef.current);
    }

    // Set initial orientation via CSS class
    boardRef.current.classList.toggle('flipped', orientation === 'black');

    updateBounds(containerRef.current);
    board.setPieces(state, fen, chess);
    state.movable.color = state.turnColor;
    redraw();

    const handleResize = () => {
      if (containerRef.current) {
        updateBounds(containerRef.current);
        redraw();
      }
    };

    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(containerRef.current);

    // Window resize listener as fallback — updateBounds sets explicit px on
    // the container, so the ResizeObserver alone won't fire when the *parent*
    // changes size via CSS viewport units (vw/vh).
    window.addEventListener('resize', handleResize);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener('resize', handleResize);
      if (premoveTimerRef.current) clearTimeout(premoveTimerRef.current);
    };
  }, [fen, redraw]);

  useEffect(() => {
    stateRef.current.orientation = orientation;
    // CSS classes handle positioning — just toggle flipped class
    if (boardRef.current) {
      boardRef.current.classList.toggle('flipped', orientation === 'black');
      // Re-render square highlights (they depend on orientation for highlight placement)
      redraw();
    }
  }, [orientation, redraw]);

  useEffect(() => {
    if (stateRef.current.premovable) {
      stateRef.current.premovable.enabled = enablePremoves;
    }
  }, [enablePremoves]);

  const premoveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const playPremoveMove = useCallback(() => {
    const state = stateRef.current;
    const chess = chessRef.current;

    if (!state.premovable?.current) return false;

    const { orig, dest } = state.premovable.current;

    // Validate piece exists and is correct color
    const piece = state.pieces.get(orig);
    if (!piece || piece.color !== state.turnColor) {
      board.clearPremove(state);
      redraw();
      return false;
    }

    // Validate legality before committing to the delay
    try {
      const tempChess = new Chess(chess.fen());
      const move = tempChess.move({ from: orig, to: dest, promotion: 'q' });
      if (!move) {
        board.clearPremove(state);
        redraw();
        return false;
      }
    } catch {
      board.clearPremove(state);
      redraw();
      return false;
    }

    // Premove is valid — execute after brief delay.
    // 80ms: enough to register opponent's move, snappier than Chess.com's 200ms
    // (their 200ms works because their full pipeline is more optimized end-to-end).
    if (premoveTimerRef.current) clearTimeout(premoveTimerRef.current);
    premoveTimerRef.current = setTimeout(() => {
      premoveTimerRef.current = null;
      const s = stateRef.current;

      // Re-check premove still exists (might have been cancelled during delay)
      if (!s.premovable?.current) return;
      if (s.premovable.current.orig !== orig || s.premovable.current.dest !== dest) return;

      // Clear premove highlights instantly — snap off, no fade (Chess.com behavior)
      board.clearPremove(s);

      try {
        const move = chess.move({ from: orig, to: dest, promotion: 'q' });
        if (!move) {
          redraw();
          return;
        }

        // Sound + animation start simultaneously (same microtask — Chess.com behavior)
        if (chess.inCheck()) {
          playCheckSound();
        } else if (move.captured) {
          playCaptureSound();
        } else {
          playMoveSound();
        }

        // Fire animation (don't await — game state updates immediately for responsiveness)
        // .then(redraw) fixes post-animation visual (e.g. pawn→queen class after promotion)
        animatePiece(orig as Key, dest as Key).then(() => redraw());

        // Sync board state while piece slides (render skips animating pieces)
        board.syncBoardFromChess(s, chess);
        s.lastMove = [orig, dest];
        s.turnColor = chess.turn() === 'w' ? 'white' : 'black';
        s.movable.color = s.turnColor;

        if (chess.inCheck()) {
          const kingSquare = board.findKing(s.pieces, s.turnColor);
          s.check = kingSquare;
        } else {
          s.check = undefined;
        }

        board.updateDests(s, chess);
        redraw();

        if (onMove) {
          onMove(orig, dest, move.san);
        }
      } catch {
        redraw();
      }
    }, 80);

    return true;
  }, [redraw, onMove, animatePiece]);

  const clearPremoveMove = useCallback(() => {
    board.clearPremove(stateRef.current);
    redraw();
  }, [redraw]);

  const hasPremoveMove = useCallback(() => {
    return !!stateRef.current.premovable?.current;
  }, []);

  useImperativeHandle(ref, () => ({
    makeMove,
    reset,
    getFen: () => chessRef.current.fen(),
    setPosition,
    setAnnotations,
    clearAnnotations,
    playPremove: playPremoveMove,
    clearPremove: clearPremoveMove,
    hasPremove: hasPremoveMove,
  }), [makeMove, reset, setPosition, setAnnotations, clearAnnotations, playPremoveMove, clearPremoveMove, hasPremoveMove]);

  const handleUserMove = useCallback((from: Key, to: Key) => {
    const state = stateRef.current;
    const chess = chessRef.current;

    try {
      const potentialCapture = state.pieces.has(to);
      const capturedPieceEl = potentialCapture ? getPieceElement(to) : null;
      let fadingClone: HTMLElement | null = null;

      if (capturedPieceEl && boardRef.current) {
        fadingClone = capturedPieceEl.cloneNode(true) as HTMLElement;
      }

      const move = chess.move({
        from: from,
        to: to,
        promotion: 'q'
      });

      if (move) {
        if (chess.inCheck()) {
          playCheckSound();
        } else if (move.captured) {
          playCaptureSound();
        } else {
          playMoveSound();
        }

        board.syncBoardFromChess(state, chess);
        state.lastMove = [from, to];
        state.turnColor = chess.turn() === 'w' ? 'white' : 'black';
        state.movable.color = state.turnColor;

        if (chess.inCheck()) {
          const kingSquare = board.findKing(state.pieces, state.turnColor);
          state.check = kingSquare;
        } else {
          state.check = undefined;
        }

        board.updateDests(state, chess);
        state.selected = undefined;
        redraw();

        if (fadingClone && move.captured && boardRef.current) {
          fadingClone.classList.add('fading');
          boardRef.current.appendChild(fadingClone);
          setTimeout(() => {
            fadingClone?.remove();
          }, 150);
        }

        if (onMove) {
          onMove(from, to, move.san);
        }
      }
    } catch {
    }
  }, [redraw, onMove, getPieceElement]);

  const handleUserMoveRef = useRef(handleUserMove);
  handleUserMoveRef.current = handleUserMove;
  const redrawRef = useRef(redraw);
  redrawRef.current = redraw;
  const getBrushFromEventRef = useRef(getBrushFromEvent);
  getBrushFromEventRef.current = getBrushFromEvent;
  const clearUserAnnotationsRef = useRef(clearUserAnnotations);
  clearUserAnnotationsRef.current = clearUserAnnotations;
  const renderUserAnnotationsRef = useRef(renderUserAnnotations);
  renderUserAnnotationsRef.current = renderUserAnnotations;
  const onSquareClickRef = useRef(onSquareClick);
  onSquareClickRef.current = onSquareClick;
  const onPieceHoverRef = useRef(onPieceHover);
  onPieceHoverRef.current = onPieceHover;

  // ─── Unified pointer event handlers (replaces 8 separate mouse/touch handlers) ───

  useEffect(() => {
    const container = boardRef.current;
    if (!container) return;

    const onPointerDown = (e: PointerEvent) => {
      const state = stateRef.current;

      // Right-click → start arrow drawing
      if (e.button === 2) {
        e.preventDefault();
        const pos = eventPosition(e);
        if (!pos) return;
        const bounds = container.getBoundingClientRect();
        const key = getKeyAtDomPos(pos, state.orientation === 'white', bounds);
        if (key) {
          const brush = getBrushFromEventRef.current(e);
          drawingArrowRef.current = { orig: key, brush };
        }
        return;
      }

      if (e.button !== 0) return;

      // Clear user annotations on left click
      if (userArrowsRef.current.length > 0 || userHighlightsRef.current.length > 0) {
        clearUserAnnotationsRef.current();
      }

      const pos = eventPosition(e);
      if (!pos) return;

      const bounds = container.getBoundingClientRect();
      const asWhite = state.orientation === 'white';
      const key = getKeyAtDomPos(pos, asWhite, bounds);
      if (!key) return;

      if (onSquareClickRef.current && onSquareClickRef.current(key)) return;

      const piece = state.pieces.get(key);
      const playerAllowed = playerColorRef.current === 'both' || piece?.color === playerColorRef.current;
      const canSelectPiece = piece && playerAllowed && (
        piece.color === state.turnColor ||
        (state.premovable?.enabled && piece.color === state.orientation)
      );

      if (canSelectPiece) {
        board.selectSquare(state, key, chessRef.current);
        drag.start(state, e, container);
        redrawRef.current();

        // Capture pointer for move/up events even outside board
        container.setPointerCapture(e.pointerId);
      } else if (state.selected) {
        const selectedPiece = state.pieces.get(state.selected);
        const selectedPlayerAllowed = playerColorRef.current === 'both' || selectedPiece?.color === playerColorRef.current;
        const selectedPieceCanMove = selectedPiece && selectedPlayerAllowed && selectedPiece.color === state.turnColor;

        if (selectedPieceCanMove) {
          const canMove = state.movable.dests?.get(state.selected)?.includes(key);
          if (canMove) {
            handleUserMoveRef.current(state.selected, key);
          } else {
            state.selected = undefined;
            redrawRef.current();
          }
        } else if (state.premovable?.enabled && selectedPiece?.color === state.orientation) {
          board.setPremove(state, state.selected, key);
          state.selected = undefined;
          redrawRef.current();
        } else {
          state.selected = undefined;
          redrawRef.current();
        }
      }
    };

    const onPointerMove = (e: PointerEvent) => {
      const state = stateRef.current;
      drag.move(state, e, container);

      // Skip hover during drag
      if (onPieceHoverRef.current && !state.draggable.current) {
        const pos = eventPosition(e);
        if (pos) {
          const bounds = container.getBoundingClientRect();
          const key = getKeyAtDomPos(pos, state.orientation === 'white', bounds);
          const hoveredKey = key || null;
          if (hoveredKey !== hoveredSquareRef.current) {
            hoveredSquareRef.current = hoveredKey;
            if (key) {
              const piece = state.pieces.get(key);
              if (piece) {
                onPieceHoverRef.current({ square: key, piece: piece.role, color: piece.color });
              } else {
                onPieceHoverRef.current(null);
              }
            } else {
              onPieceHoverRef.current(null);
            }
          }
        }
      }
    };

    const onPointerUp = (e: PointerEvent) => {
      const state = stateRef.current;

      // Right-click release → complete arrow
      if (e.button === 2 && drawingArrowRef.current) {
        e.preventDefault();
        const pos = eventPosition(e);
        const bounds = container.getBoundingClientRect();
        const asWhite = state.orientation === 'white';
        const dest = pos ? getKeyAtDomPos(pos, asWhite, bounds) : null;
        const { orig, brush } = drawingArrowRef.current;

        if (dest && dest !== orig) {
          const idx = userArrowsRef.current.findIndex(a => a.from === orig && a.to === dest && a.brush === brush);
          if (idx >= 0) userArrowsRef.current.splice(idx, 1);
          else userArrowsRef.current.push({ from: orig, to: dest, brush });
        } else {
          const idx = userHighlightsRef.current.findIndex(h => h.key === orig && h.brush === brush);
          if (idx >= 0) userHighlightsRef.current.splice(idx, 1);
          else userHighlightsRef.current.push({ key: orig, brush });
        }

        drawingArrowRef.current = null;
        renderUserAnnotationsRef.current();
        return;
      }

      // Left-click release → complete drag/move
      const cur = state.draggable.current;
      if (cur && cur.started) {
        const bounds = container.getBoundingClientRect();
        const asWhite = state.orientation === 'white';
        const pos = eventPosition(e);
        const playerAllowed = playerColorRef.current === 'both' || cur.piece.color === playerColorRef.current;
        const pieceCanMove = playerAllowed && cur.piece.color === state.turnColor;

        if (pos) {
          const dest = getKeyAtDomPos(pos, asWhite, bounds);
          if (dest && dest !== cur.orig) {
            if (pieceCanMove) {
              const canMove = state.movable.dests?.get(cur.orig)?.includes(dest);
              if (canMove) {
                drag.cancel(state, container);
                handleUserMoveRef.current(cur.orig, dest);
                return;
              }
            } else if (state.premovable?.enabled && cur.piece.color === state.orientation) {
              drag.cancel(state, container);
              board.setPremove(state, cur.orig, dest);
              redrawRef.current();
              return;
            }
          }
        }
      }

      drag.cancel(state, container);
      redrawRef.current();
    };

    const onContextMenu = (e: Event) => {
      e.preventDefault();
    };

    container.addEventListener('pointerdown', onPointerDown);
    container.addEventListener('pointermove', onPointerMove);
    container.addEventListener('pointerup', onPointerUp);
    container.addEventListener('contextmenu', onContextMenu);

    return () => {
      container.removeEventListener('pointerdown', onPointerDown);
      container.removeEventListener('pointermove', onPointerMove);
      container.removeEventListener('pointerup', onPointerUp);
      container.removeEventListener('contextmenu', onContextMenu);
    };
  }, []); // Empty deps — reads from refs, stable forever

  const ranks = orientation === 'white' ? ['8','7','6','5','4','3','2','1'] : ['1','2','3','4','5','6','7','8'];
  const files = orientation === 'white' ? ['a','b','c','d','e','f','g','h'] : ['h','g','f','e','d','c','b','a'];

  return (
    <div ref={containerRef} className="cg-wrap">
      <div
        ref={boardRef}
        className="cg-board"
        style={{ touchAction: 'none' }}
      >
        <div className="ghost" style={{ visibility: 'hidden' }} />
        <div className="drag-hover" style={{ visibility: 'hidden' }} />
      </div>
      {/* Board coordinates */}
      <div className="board-coords" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 4 }}>
        {/* Rank numbers — left edge */}
        {ranks.map((rank, i) => (
          <span
            key={`r-${rank}`}
            style={{
              position: 'absolute',
              left: '2px',
              top: `${i * 12.5 + 0.5}%`,
              fontSize: '13px',
              fontWeight: 800,
              fontFamily: 'system-ui, sans-serif',
              lineHeight: 1,
              color: i % 2 === 0 ? '#a07050' : '#f0e4d0',
            }}
          >
            {rank}
          </span>
        ))}
        {/* File letters — bottom edge */}
        {files.map((file, i) => (
          <span
            key={`f-${file}`}
            style={{
              position: 'absolute',
              bottom: '1px',
              left: `${i * 12.5 + 12.5 - 1.5}%`,
              transform: 'translateX(-100%)',
              fontSize: '10px',
              fontWeight: 700,
              fontFamily: 'sans-serif',
              lineHeight: 1,
              color: i % 2 === 1 ? '#b88863' : '#eddfc8',
            }}
          >
            {file}
          </span>
        ))}
      </div>
    </div>
  );
});

Chessboard.displayName = 'Chessboard';
