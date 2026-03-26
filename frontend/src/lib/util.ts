// Utility functions - ported from chessground/util.ts

import type { Key, Pos, NumberPair, MouchEvent, Bounds } from './types';
import { files, ranks } from './types';

export const allKeys: readonly Key[] = files.flatMap(f => 
  ranks.map(r => `${f}${r}` as Key)
);

export function key2pos(key: Key): Pos {
  return [
    key.charCodeAt(0) - 97, // a=0, h=7
    key.charCodeAt(1) - 49  // 1=0, 8=7
  ];
}

export function pos2key(pos: Pos): Key | undefined {
  if (pos[0] < 0 || pos[0] > 7 || pos[1] < 0 || pos[1] > 7) return undefined;
  return `${String.fromCharCode(97 + pos[0])}${pos[1] + 1}` as Key;
}

export function posToTranslate(bounds: DOMRect) {
  return (pos: Pos, asWhite: boolean): NumberPair => [
    ((asWhite ? pos[0] : 7 - pos[0]) * bounds.width) / 8,
    ((asWhite ? 7 - pos[1] : pos[1]) * bounds.height) / 8,
  ];
}

export function translate(el: HTMLElement, pos: NumberPair): void {
  el.style.transform = `translate(${pos[0]}px, ${pos[1]}px)`;
}

export function translateDrag(el: HTMLElement, pos: NumberPair): void {
  el.style.transform = `translate(${pos[0]}px, ${pos[1]}px) scale(1.08)`;
}

export function eventPosition(e: MouchEvent): NumberPair | undefined {
  if ('clientX' in e && (e.clientX || e.clientX === 0)) {
    return [e.clientX, e.clientY];
  }
  if ('touches' in e && e.touches?.[0]) {
    return [e.touches[0].clientX, e.touches[0].clientY];
  }
  return undefined;
}

export function isRightButton(e: MouchEvent): boolean {
  return 'button' in e && e.button === 2;
}

export function memo<A>(f: () => A): Bounds {
  let v: A | undefined;
  const ret: any = () => {
    if (v === undefined) v = f();
    return v;
  };
  ret.clear = () => {
    v = undefined;
  };
  return ret;
}

export function distanceSq(pos1: Pos, pos2: Pos): number {
  const dx = pos1[0] - pos2[0];
  const dy = pos1[1] - pos2[1];
  return dx * dx + dy * dy;
}

export function squareCenter(key: Key, asWhite: boolean, bounds: DOMRect): NumberPair {
  const pos = key2pos(key);
  const translate = posToTranslate(bounds);
  const [x, y] = translate(pos, asWhite);
  return [
    x + bounds.width / 16,
    y + bounds.height / 16
  ];
}

export function getKeyAtDomPos(
  pos: NumberPair,
  asWhite: boolean,
  bounds: DOMRect
): Key | undefined {
  let file = Math.floor((8 * (pos[0] - bounds.left)) / bounds.width);
  if (!asWhite) file = 7 - file;
  
  let rank = 7 - Math.floor((8 * (pos[1] - bounds.top)) / bounds.height);
  if (!asWhite) rank = 7 - rank;
  
  return file >= 0 && file < 8 && rank >= 0 && rank < 8
    ? pos2key([file, rank])
    : undefined;
}

export function samePiece(p1: { role: string; color: string }, p2: { role: string; color: string }): boolean {
  return p1.role === p2.role && p1.color === p2.color;
}

export function createEl(tag: string, className?: string): HTMLElement {
  const el = document.createElement(tag);
  if (className) el.className = className;
  return el;
}

export function setVisible(el: HTMLElement, visible: boolean): void {
  el.style.visibility = visible ? 'visible' : 'hidden';
}

export function opposite(c: 'white' | 'black'): 'white' | 'black' {
  return c === 'white' ? 'black' : 'white';
}
