"""
Opening book adapter — thin sync wrapper around the Lichess Explorer API.

Filters to a narrow, stability-biased repertoire:
  vs 1.e4: Caro-Kann family
  vs 1.d4 / 1.c4 / 1.Nf3: QGD / Slav family

Does NOT call the EvalMax module directly. Reimplements the Lichess Explorer
call as a sync httpx request to avoid async/aiohttp dependency.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import chess
import httpx

EXPLORER_URL = "https://explorer.lichess.ovh/lichess"
EXPLORER_TIMEOUT = 3.0
MIN_GAMES = 50  # only play moves seen in 50+ master games
MAX_PLY = 12  # hand off to proposer after ply 12 (move 6 for each side)

# ---------------------------------------------------------------------------
# Repertoire filters
# ---------------------------------------------------------------------------

# Opening names from the Lichess Explorer that belong to our repertoire.
# The Explorer returns an "opening" field with a name string.
# We match these prefixes to stay inside our chosen systems.

CARO_KANN_PREFIXES = [
    "Caro-Kann",
]

QGD_SLAV_PREFIXES = [
    "Queen's Gambit Declined",
    "Queen's Gambit Accepted",
    "Queen's Gambit",  # plain "Queen's Gambit" before it specifies a sub-line
    "Slav",
    "Semi-Slav",
    "Queen's Pawn",
]

# Explicit first-move responses (before the explorer has opening names)
FIRST_MOVE_RESPONSES = {
    # If White plays 1.e4, we play 1...c6 (Caro-Kann)
    "e2e4": "c7c6",
    # If White plays 1.d4, we play 1...d5
    "d2d4": "d7d5",
    # If White plays 1.c4, we play 1...e6 (can transpose to QGD)
    "c2c4": "e7e6",
    # If White plays 1.Nf3, we play 1...d5
    "g1f3": "d7d5",
}


@dataclass
class BookMove:
    uci: str
    san: str
    games: int
    white_wins: int
    draws: int
    black_wins: int

    @property
    def score(self) -> float:
        """Win rate for Black (higher = better for us)."""
        total = self.white_wins + self.draws + self.black_wins
        if total == 0:
            return 0.0
        # Score: wins count 1.0, draws count 0.5
        return (self.black_wins + 0.5 * self.draws) / total


@dataclass
class BookResult:
    move: BookMove
    opening_name: Optional[str]
    confidence: str  # "high", "medium", "low"


# ---------------------------------------------------------------------------
# Lichess Explorer (sync)
# ---------------------------------------------------------------------------

_cache: dict[str, Optional[dict]] = {}


def _get_token() -> str:
    """Get Lichess token from env vars or local .env. Never reads foreign project files."""
    token = os.environ.get("LICHESS_TOKEN") or os.environ.get("VITE_LICHESS_TOKEN") or ""
    if token.strip():
        return token.strip()
    # Fallback: read from our own backend/.env (not foreign projects)
    local_env = Path(__file__).parent / ".env"
    if local_env.exists():
        for line in local_env.read_text().splitlines():
            if line.startswith("LICHESS_TOKEN="):
                return line.split("=", 1)[1].strip()
    return ""


def _fetch_explorer(fen: str) -> Optional[dict]:
    """Fetch position data from Lichess Explorer API (sync)."""
    # Include side-to-move, castling, and en passant in cache key (not just pieces)
    parts = fen.split(" ")
    cache_key = " ".join(parts[:4]) if len(parts) >= 4 else fen
    if cache_key in _cache:
        return _cache[cache_key]

    token = _get_token()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = httpx.get(
            EXPLORER_URL,
            params={
                "fen": fen,
                "ratings": "2200,2500",
                "speeds": "rapid,classical,correspondence",
                "moves": 12,
            },
            headers=headers,
            timeout=EXPLORER_TIMEOUT,
        )
        if resp.status_code != 200:
            _cache[cache_key] = None
            return None

        data = resp.json()
        _cache[cache_key] = data
        return data
    except Exception:
        _cache[cache_key] = None
        return None


def _parse_moves(data: dict) -> list[BookMove]:
    moves = []
    for m in data.get("moves", []):
        white = m.get("white", 0)
        draws = m.get("draws", 0)
        black = m.get("black", 0)
        total = white + draws + black
        if total < MIN_GAMES:
            continue
        moves.append(BookMove(
            uci=m.get("uci", ""),
            san=m.get("san", ""),
            games=total,
            white_wins=white,
            draws=draws,
            black_wins=black,
        ))
    return moves


def _opening_name(data: dict) -> Optional[str]:
    opening = data.get("opening")
    if opening and isinstance(opening, dict):
        return opening.get("name")
    return None


def _is_in_repertoire(opening_name: Optional[str], board: chess.Board) -> bool:
    """Check if the current position belongs to our repertoire families."""
    if opening_name is None:
        # No opening name yet (very early moves) — allow if it's ply <= 2
        return board.ply() <= 2

    name_lower = opening_name.lower()

    # Check Caro-Kann family (vs e4)
    for prefix in CARO_KANN_PREFIXES:
        if prefix.lower() in name_lower:
            return True

    # Check QGD/Slav family (vs d4)
    for prefix in QGD_SLAV_PREFIXES:
        if prefix.lower() in name_lower:
            return True

    return False


def _move_keeps_repertoire(board: chess.Board, move: chess.Move) -> bool:
    """Only keep book moves whose resulting position stays inside the repertoire."""
    next_board = board.copy()
    next_board.push(move)

    data = _fetch_explorer(next_board.fen())
    if data is None:
        return False

    next_opening_name = _opening_name(data)
    return _is_in_repertoire(next_opening_name, next_board)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_book_move(board: chess.Board) -> Optional[BookResult]:
    """
    Get a book move for the current position, filtered to our repertoire.

    Returns None if:
    - position is past MAX_PLY
    - no confident repertoire hit exists
    - position is outside our opening families
    """
    if board.turn != chess.BLACK:
        return None  # we only play Black

    if board.ply() > MAX_PLY:
        return None  # hand off to proposer

    fen = board.fen()

    # Special case: first move response (ply 1)
    if board.ply() == 1:
        # Find White's last move
        # The board is at ply 1 = after White's first move
        # We need to figure out what White played
        for uci, response_uci in FIRST_MOVE_RESPONSES.items():
            move = chess.Move.from_uci(response_uci)
            if move in board.legal_moves:
                # Verify the White move matches
                # Check if the current position matches this White opening
                test = chess.Board()
                try:
                    test.push_uci(uci)
                    if test.fen().split(" ")[0] == fen.split(" ")[0]:
                        san = board.san(move)
                        return BookResult(
                            move=BookMove(uci=response_uci, san=san, games=999999,
                                          white_wins=0, draws=0, black_wins=0),
                            opening_name=None,
                            confidence="high",
                        )
                except (chess.InvalidMoveError, chess.IllegalMoveError):
                    continue
        # Unknown first move — still try the explorer
        pass

    # Fetch from Lichess Explorer
    data = _fetch_explorer(fen)
    if data is None:
        return None

    opening_name = _opening_name(data)

    # Check repertoire fit
    if not _is_in_repertoire(opening_name, board):
        return None  # outside our families — hand off to proposer

    moves = _parse_moves(data)
    if not moves:
        return None

    # Filter to legal moves that keep us inside the repertoire after we play them.
    legal_uci = {m.uci() for m in board.legal_moves}
    filtered = []
    for m in moves:
        if m.uci not in legal_uci:
            continue
        try:
            move = chess.Move.from_uci(m.uci)
        except ValueError:
            continue
        if _move_keeps_repertoire(board, move):
            filtered.append(m)
    moves = filtered
    if not moves:
        return None

    # Pick the best move for Black.
    # Primary: most games played (popularity = proven in master play).
    # Tiebreak: highest Black score.
    # Ignore moves with very few games — noise in the data.
    moves.sort(key=lambda m: (m.games, m.score), reverse=True)
    best = moves[0]

    # Confidence: high if clearly dominant, medium if reasonable
    if len(moves) == 1:
        confidence = "high"
    elif best.games > moves[1].games * 2:
        confidence = "high"
    elif best.games > moves[1].games:
        confidence = "medium"
    else:
        confidence = "low"

    # Only play if confidence is medium or high
    if confidence == "low":
        return None  # hand off to proposer — no clear best move

    return BookResult(
        move=best,
        opening_name=opening_name,
        confidence=confidence,
    )
