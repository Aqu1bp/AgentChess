"""
AgentChess Perception Layer

Provides ground-truth board facts via python-chess. NO tactical scanning —
agents must find tactics themselves. This layer only answers factual questions:
where are pieces, what attacks what, are moves legal.

CLI usage:
    python3 perception.py state --fen "..."
    python3 perception.py legal --fen "..."
    python3 perception.py simulate --fen "..." --move Nf3 --move Nc6
    python3 perception.py query --fen "..." --square e4
    python3 perception.py state --fen "..." --json
"""

import argparse
import json
import re
import sys
from typing import Optional

import chess

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PIECE_VALUES = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}

PIECE_NAMES = {
    chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
    chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king",
}

PIECE_SYMBOLS = {
    chess.PAWN: "", chess.KNIGHT: "N", chess.BISHOP: "B",
    chess.ROOK: "R", chess.QUEEN: "Q", chess.KING: "K",
}

UCI_PATTERN = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


def piece_label(piece: chess.Piece) -> str:
    """e.g. 'white knight' or 'black pawn'."""
    color = "white" if piece.color == chess.WHITE else "black"
    return f"{color} {PIECE_NAMES[piece.piece_type]}"


def square_name(sq: int) -> str:
    return chess.square_name(sq)


def piece_at_label(board: chess.Board, sq: int) -> str:
    """Short label like 'Nb1' or 'pe4'."""
    piece = board.piece_at(sq)
    if piece is None:
        return "empty"
    sym = PIECE_SYMBOLS[piece.piece_type]
    if piece.piece_type == chess.PAWN:
        sym = ""
    return f"{sym}{square_name(sq)}"


def get_legal_attackers(board: chess.Board, sq: int, color: chess.Color) -> list[int]:
    """
    Get pieces of `color` that can legally capture on `sq`.
    Handles pins correctly by using a hypothetical board where it's the attacker's turn,
    with an enemy piece placed on the target square to check legal captures.
    """
    # Create hypothetical board where it's the attacker's turn
    hypo = board.copy()
    # Place an enemy piece on the target so captures are possible
    target_piece = hypo.piece_at(sq)
    enemy_color = not color
    if target_piece is None or target_piece.color == color:
        # Place a dummy enemy pawn so we can check legal captures onto this square
        hypo.set_piece_at(sq, chess.Piece(chess.PAWN, enemy_color))
    hypo.turn = color
    # Clear en passant to avoid false positives on hypothetical board
    hypo.ep_square = board.ep_square if board.turn == color else None

    attackers = []
    for move in hypo.legal_moves:
        if move.to_square == sq:
            piece = hypo.piece_at(move.from_square)
            if piece and piece.color == color:
                attackers.append(move.from_square)
    return attackers


def get_defenders(board: chess.Board, sq: int, color: chess.Color) -> list[int]:
    """
    Get pieces of `color` that defend `sq`.
    Uses a hypothetical capture approach to correctly handle pins:
    place an enemy piece on the square, then check legal captures.
    """
    piece = board.piece_at(sq)
    if piece is None or piece.color != color:
        return []

    # Create hypothetical board with an enemy pawn on the square
    hypo = board.copy()
    enemy_color = not color
    hypo.set_piece_at(sq, chess.Piece(chess.PAWN, enemy_color))
    # Set it to be the defender's turn
    hypo.turn = color

    defenders = []
    for move in hypo.legal_moves:
        if move.to_square == sq:
            defenders.append(move.from_square)
    return defenders


def material_count(board: chess.Board, color: chess.Color) -> int:
    total = 0
    for pt in [chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN]:
        total += len(board.pieces(pt, color)) * PIECE_VALUES[pt]
    return total


def detect_game_phase(board: chess.Board) -> str:
    """Classify position as opening, middlegame, or endgame."""
    total_material = material_count(board, chess.WHITE) + material_count(board, chess.BLACK)
    queens = len(board.pieces(chess.QUEEN, chess.WHITE)) + len(board.pieces(chess.QUEEN, chess.BLACK))
    minor_major = (
        len(board.pieces(chess.KNIGHT, chess.WHITE)) + len(board.pieces(chess.KNIGHT, chess.BLACK))
        + len(board.pieces(chess.BISHOP, chess.WHITE)) + len(board.pieces(chess.BISHOP, chess.BLACK))
        + len(board.pieces(chess.ROOK, chess.WHITE)) + len(board.pieces(chess.ROOK, chess.BLACK))
    )
    if total_material <= 26 or (queens == 0 and minor_major <= 4):
        return "endgame"
    if board.fullmove_number <= 10:
        return "opening"
    return "middlegame"


def pawn_structure(board: chess.Board, color: chess.Color) -> dict:
    """Analyze pawn structure: isolated, doubled, passed, connected pawns."""
    pawns = list(board.pieces(chess.PAWN, color))
    pawn_files = [chess.square_file(sq) for sq in pawns]

    isolated = []
    doubled = []
    passed = []
    connected = []
    backward = []

    for sq in pawns:
        f = chess.square_file(sq)
        r = chess.square_rank(sq)
        sq_name = square_name(sq)

        # Doubled: another friendly pawn on same file
        same_file = [s for s in pawns if chess.square_file(s) == f and s != sq]
        if same_file:
            doubled.append(sq_name)

        # Isolated: no friendly pawns on adjacent files
        adj_files = [af for af in [f - 1, f + 1] if 0 <= af <= 7]
        has_neighbor = any(af in pawn_files for af in adj_files)
        if not has_neighbor:
            isolated.append(sq_name)

        # Connected: friendly pawn on adjacent file AND adjacent rank
        is_connected = False
        for other_sq in pawns:
            if other_sq == sq:
                continue
            of = chess.square_file(other_sq)
            orank = chess.square_rank(other_sq)
            if abs(of - f) == 1 and abs(orank - r) <= 1:
                is_connected = True
                break
        if is_connected:
            connected.append(sq_name)

        # Passed: no enemy pawns on same or adjacent files ahead
        enemy_pawns = list(board.pieces(chess.PAWN, not color))
        is_passed = True
        for ep in enemy_pawns:
            ef = chess.square_file(ep)
            er = chess.square_rank(ep)
            if abs(ef - f) <= 1:
                if color == chess.WHITE and er > r:
                    is_passed = False
                    break
                elif color == chess.BLACK and er < r:
                    is_passed = False
                    break
        if is_passed:
            passed.append(sq_name)

        # Backward: no friendly pawns on adjacent files behind/equal to support advance,
        # AND the stop square (one ahead) is controlled by an enemy pawn
        if not isolated:  # isolated pawns are already flagged
            friendly_behind = False
            for other_sq in pawns:
                if other_sq == sq:
                    continue
                of = chess.square_file(other_sq)
                orank = chess.square_rank(other_sq)
                if abs(of - f) == 1:
                    if color == chess.WHITE and orank <= r:
                        friendly_behind = True
                        break
                    elif color == chess.BLACK and orank >= r:
                        friendly_behind = True
                        break
            if not friendly_behind:
                # Check if stop square is controlled by enemy pawn
                stop_rank = r + 1 if color == chess.WHITE else r - 1
                if 0 <= stop_rank <= 7:
                    stop_sq = chess.square(f, stop_rank)
                    enemy_controls_stop = False
                    for ep in enemy_pawns:
                        ef = chess.square_file(ep)
                        er = chess.square_rank(ep)
                        if abs(ef - f) == 1:
                            if color == chess.WHITE and er == stop_rank + 1:
                                enemy_controls_stop = True
                                break
                            elif color == chess.BLACK and er == stop_rank - 1:
                                enemy_controls_stop = True
                                break
                    if enemy_controls_stop:
                        backward.append(sq_name)

    return {
        "squares": [square_name(sq) for sq in pawns],
        "isolated": isolated,
        "doubled": doubled,
        "passed": passed,
        "connected": connected,
        "backward": backward,
    }


def parse_move(board: chess.Board, move_str: str) -> Optional[chess.Move]:
    """Parse a move string as UCI or SAN. Returns None if invalid."""
    move_str = move_str.strip()
    # Try UCI first
    if UCI_PATTERN.match(move_str):
        move = chess.Move.from_uci(move_str)
        if move in board.legal_moves:
            return move
    # Try SAN
    try:
        return board.parse_san(move_str)
    except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
        return None


def capture_info(board: chess.Board, move: chess.Move) -> tuple[str | None, int]:
    """Return captured piece label and value, including en passant."""
    if not board.is_capture(move):
        return None, 0

    captured_piece = board.piece_at(move.to_square)
    if captured_piece:
        return piece_label(captured_piece), PIECE_VALUES.get(captured_piece.piece_type, 0)

    if board.is_en_passant(move):
        return "pawn (en passant)", PIECE_VALUES[chess.PAWN]

    return "unknown piece", 0


def material_balance(board: chess.Board, color: chess.Color) -> int:
    """Material balance from `color`'s perspective."""
    return material_count(board, color) - material_count(board, not color)


def collect_hanging_pieces(board: chess.Board, color: chess.Color, *, min_value: int = 0) -> list[dict]:
    """Pieces for `color` that are attacked and undefended."""
    pieces = []
    enemy = not color
    for pt in [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN]:
        value = PIECE_VALUES[pt]
        if value < min_value:
            continue
        for sq in sorted(board.pieces(pt, color)):
            attackers = get_legal_attackers(board, sq, enemy)
            defenders = get_defenders(board, sq, color)
            if attackers and not defenders:
                pieces.append({
                    "square": square_name(sq),
                    "piece": piece_label(board.piece_at(sq)),
                    "value": value,
                    "attackers": [piece_at_label(board, a) for a in attackers],
                    "defenders": [piece_at_label(board, d) for d in defenders],
                })
    return pieces


def safe_moves_for_piece(board: chess.Board, from_square: int, color: chess.Color) -> list[dict]:
    """Legal moves for a piece that land on squares not attacked by the opponent."""
    enemy = not color
    safe_moves = []
    for move in board.legal_moves:
        if move.from_square != from_square:
            continue
        next_board = board.copy()
        next_board.push(move)
        piece = next_board.piece_at(move.to_square)
        if piece is None or piece.color != color:
            continue
        attackers = get_legal_attackers(next_board, move.to_square, enemy)
        if attackers:
            continue
        safe_moves.append({
            "san": board.san(move),
            "uci": move.uci(),
            "square": square_name(move.to_square),
        })
    return safe_moves


def best_immediate_recapture_balance(board: chess.Board, color: chess.Color) -> int:
    """Best one-ply capture balance `color` can reach from the current position."""
    best = material_balance(board, color)
    for move in board.legal_moves:
        if not board.is_capture(move):
            continue
        next_board = board.copy()
        next_board.push(move)
        best = max(best, material_balance(next_board, color))
    return best


def worst_capture_balance_after_response(board: chess.Board, color: chess.Color) -> int:
    """
    Worst balance `color` can be forced into after the side to move captures once,
    followed by `color`'s best immediate capturing reply.
    """
    worst = material_balance(board, color)
    for move in board.legal_moves:
        if not board.is_capture(move):
            continue
        next_board = board.copy()
        next_board.push(move)
        worst = min(worst, best_immediate_recapture_balance(next_board, color))
    return worst


def best_evasion_balance_after_check(board: chess.Board, color: chess.Color) -> int:
    """
    Best balance `color` can preserve from a checked position after one legal
    evasion and the opponent's strongest immediate capture sequence.
    """
    best = -10_000
    found_evasion = False
    for move in board.legal_moves:
        found_evasion = True
        next_board = board.copy()
        next_board.push(move)
        best = max(best, worst_capture_balance_after_response(next_board, color))
    return best if found_evasion else material_balance(board, color)


def can_capture_reply_piece_safely(board: chess.Board, target_square: int, color: chess.Color) -> bool:
    """
    Return True if `color` can legally capture the piece on `target_square` and
    stay at or above the current material balance after the opponent's strongest
    immediate capture sequence.
    """
    baseline = material_balance(board, color)
    for move in board.legal_moves:
        if move.to_square != target_square or not board.is_capture(move):
            continue
        next_board = board.copy()
        next_board.push(move)
        if worst_capture_balance_after_response(next_board, color) >= baseline:
            return True
    return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_state(board: chess.Board, as_json: bool = False) -> str | dict:
    """Full board state description."""
    phase = detect_game_phase(board)
    white_mat = material_count(board, chess.WHITE)
    black_mat = material_count(board, chess.BLACK)
    mat_diff = white_mat - black_mat

    # Castling
    castling = []
    if board.has_kingside_castling_rights(chess.WHITE):
        castling.append("White O-O")
    if board.has_queenside_castling_rights(chess.WHITE):
        castling.append("White O-O-O")
    if board.has_kingside_castling_rights(chess.BLACK):
        castling.append("Black O-O")
    if board.has_queenside_castling_rights(chess.BLACK):
        castling.append("Black O-O-O")

    # En passant
    ep_square = board.ep_square

    # Pieces with attack/defense info
    def describe_pieces(color: chess.Color) -> list[dict]:
        pieces_info = []
        enemy = not color
        for pt in [chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN]:
            for sq in sorted(board.pieces(pt, color)):
                attackers = get_legal_attackers(board, sq, enemy)
                defenders = get_defenders(board, sq, color)
                info = {
                    "square": square_name(sq),
                    "piece": PIECE_NAMES[pt],
                    "attackers": [piece_at_label(board, a) for a in attackers],
                    "defenders": [piece_at_label(board, d) for d in defenders],
                }
                # Add pawn shelter for kings
                if pt == chess.KING:
                    shelter = []
                    king_file = chess.square_file(sq)
                    king_rank = chess.square_rank(sq)
                    shelter_dir = 1 if color == chess.WHITE else -1
                    for df in [-1, 0, 1]:
                        sf = king_file + df
                        if 0 <= sf <= 7:
                            for dr in [1, 2]:
                                sr = king_rank + shelter_dir * dr
                                if 0 <= sr <= 7:
                                    shelter_sq = chess.square(sf, sr)
                                    p = board.piece_at(shelter_sq)
                                    if p and p.piece_type == chess.PAWN and p.color == color:
                                        shelter.append(square_name(shelter_sq))
                    if shelter:
                        info["pawn_shelter"] = shelter
                pieces_info.append(info)
        return pieces_info

    white_pieces = describe_pieces(chess.WHITE)
    black_pieces = describe_pieces(chess.BLACK)

    # Legal moves grouped
    legal_moves = list(board.legal_moves)
    captures = []
    checks = []
    other = []
    for move in legal_moves:
        san = board.san(move)
        is_capture = board.is_capture(move)
        board.push(move)
        is_check = board.is_check()
        board.pop()
        if is_check:
            checks.append(san)
        if is_capture:
            captures.append(san)
        if not is_capture and not is_check:
            other.append(san)

    w_pawn = pawn_structure(board, chess.WHITE)
    b_pawn = pawn_structure(board, chess.BLACK)

    turn = "white" if board.turn == chess.WHITE else "black"

    data = {
        "fen": board.fen(),
        "turn": turn,
        "fullmove": board.fullmove_number,
        "halfmove_clock": board.halfmove_clock,
        "phase": phase,
        "material": {"white": white_mat, "black": black_mat, "diff": mat_diff},
        "castling": castling,
        "en_passant": square_name(ep_square) if ep_square is not None else None,
        "in_check": board.is_check(),
        "is_checkmate": board.is_checkmate(),
        "is_stalemate": board.is_stalemate(),
        "is_insufficient": board.is_insufficient_material(),
        "is_fifty_moves": board.can_claim_fifty_moves(),
        "is_repetition": board.can_claim_threefold_repetition(),
        "white_pieces": white_pieces,
        "black_pieces": black_pieces,
        "white_pawns": w_pawn,
        "black_pawns": b_pawn,
        "legal_move_count": len(legal_moves),
        "captures": captures,
        "checks": checks,
        "other_moves": other,
    }

    if as_json:
        return data

    # Natural language output
    lines = []
    lines.append("=== BOARD STATE ===")
    mat_desc = f"White {white_mat} vs Black {black_mat}"
    if mat_diff > 0:
        mat_desc += f" (White +{mat_diff})"
    elif mat_diff < 0:
        mat_desc += f" (Black +{-mat_diff})"
    else:
        mat_desc += " (equal)"

    lines.append(f"Turn: {turn.capitalize()} | Move: {board.fullmove_number} | Phase: {phase.capitalize()}")
    lines.append(f"Material: {mat_desc}")
    cast_str = ", ".join(castling) if castling else "none"
    lines.append(f"Castling: {cast_str}")
    if ep_square is not None:
        lines.append(f"En passant: {square_name(ep_square)}")
    if board.is_check():
        lines.append("*** IN CHECK ***")

    for color_name, pieces in [("WHITE", white_pieces), ("BLACK", black_pieces)]:
        lines.append(f"\n{color_name} PIECES:")
        for p in pieces:
            atk = ", ".join(p["attackers"]) if p["attackers"] else "none"
            dfn = ", ".join(p["defenders"]) if p["defenders"] else "none"
            shelter_str = ""
            if "pawn_shelter" in p:
                shelter_str = f" | pawn shelter: {', '.join(p['pawn_shelter'])}"
            lines.append(f"  {p['piece'].capitalize()} {p['square']} | attacked by: {atk} | defended by: {dfn}{shelter_str}")

    for label, pdata in [("WHITE PAWNS", w_pawn), ("BLACK PAWNS", b_pawn)]:
        lines.append(f"\n{label}: {', '.join(pdata['squares']) or 'none'}")
        if pdata["isolated"]:
            lines.append(f"  Isolated: {', '.join(pdata['isolated'])}")
        if pdata["doubled"]:
            lines.append(f"  Doubled: {', '.join(pdata['doubled'])}")
        if pdata["passed"]:
            lines.append(f"  Passed: {', '.join(pdata['passed'])}")
        if pdata["connected"]:
            lines.append(f"  Connected: {', '.join(pdata['connected'])}")
        if pdata["backward"]:
            lines.append(f"  Backward: {', '.join(pdata['backward'])}")

    lines.append(f"\nLEGAL MOVES ({len(legal_moves)}):")
    if captures:
        lines.append(f"  Captures: {', '.join(captures)}")
    if checks:
        lines.append(f"  Checks: {', '.join(checks)}")
    if other:
        lines.append(f"  Other: {', '.join(other)}")

    return "\n".join(lines)


def cmd_legal(board: chess.Board, as_json: bool = False) -> str | dict:
    """List all legal moves with SAN and UCI."""
    moves = []
    for move in board.legal_moves:
        san = board.san(move)
        uci = move.uci()
        is_capture = board.is_capture(move)
        board.push(move)
        is_check = board.is_check()
        is_mate = board.is_checkmate()
        board.pop()
        moves.append({
            "san": san,
            "uci": uci,
            "capture": is_capture,
            "check": is_check,
            "checkmate": is_mate,
        })

    if as_json:
        return {"fen": board.fen(), "move_count": len(moves), "moves": moves}

    lines = [f"Legal moves ({len(moves)}):"]
    for m in moves:
        flags = []
        if m["checkmate"]:
            flags.append("MATE")
        elif m["check"]:
            flags.append("check")
        if m["capture"]:
            flags.append("capture")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {m['san']} ({m['uci']}){flag_str}")
    return "\n".join(lines)


def cmd_simulate(board: chess.Board, moves: list[str], as_json: bool = False) -> str | dict:
    """Simulate a sequence of moves and report legality + resulting positions."""
    sim_board = board.copy()
    results = []
    all_legal = True

    for i, move_str in enumerate(moves):
        move = parse_move(sim_board, move_str)
        if move is None:
            results.append({
                "move_number": i + 1,
                "input": move_str,
                "legal": False,
                "error": f"Illegal or unparseable move: {move_str}",
                "fen": sim_board.fen(),
            })
            all_legal = False
            break

        san = sim_board.san(move)
        is_capture = sim_board.is_capture(move)
        captured = None
        if is_capture:
            captured_piece = sim_board.piece_at(move.to_square)
            if captured_piece:
                captured = piece_label(captured_piece)
            elif sim_board.is_en_passant(move):
                captured = "pawn (en passant)"

        sim_board.push(move)
        is_check = sim_board.is_check()
        is_mate = sim_board.is_checkmate()

        results.append({
            "move_number": i + 1,
            "input": move_str,
            "san": san,
            "uci": move.uci(),
            "legal": True,
            "check": is_check,
            "checkmate": is_mate,
            "capture": captured,
            "fen": sim_board.fen(),
        })

    data = {
        "starting_fen": board.fen(),
        "all_legal": all_legal,
        "moves_checked": len(results),
        "final_fen": sim_board.fen(),
        "results": results,
    }

    if as_json:
        return data

    lines = [f"Simulating from: {board.fen()}"]
    for r in results:
        if r["legal"]:
            flags = []
            if r.get("checkmate"):
                flags.append("CHECKMATE")
            elif r.get("check"):
                flags.append("check")
            if r.get("capture"):
                flags.append(f"captures {r['capture']}")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            prefix = f"{r['move_number']}." if sim_board.turn == chess.WHITE else f"{r['move_number']}..."
            # Fix prefix based on who played
            turn_before = chess.WHITE if r["move_number"] % 2 == 1 else chess.BLACK
            if board.turn == chess.BLACK:
                turn_before = chess.BLACK if r["move_number"] % 2 == 1 else chess.WHITE
            lines.append(f"  {r['move_number']}. {r['san']} -- LEGAL{flag_str}")
            lines.append(f"     Position: {r['fen']}")
        else:
            lines.append(f"  {r['move_number']}. {r['input']} -- ILLEGAL: {r['error']}")
            lines.append(f"     Position (unchanged): {r['fen']}")

    status = "All moves legal" if all_legal else f"Line breaks at move {len(results)}"
    lines.append(f"\nRESULT: {status}. Final position: {sim_board.fen()}")
    return "\n".join(lines)


def cmd_query(board: chess.Board, square_str: str, as_json: bool = False) -> str | dict:
    """Query detailed info about a specific square."""
    try:
        sq = chess.parse_square(square_str.lower())
    except ValueError:
        print(f"Invalid square: {square_str}", file=sys.stderr)
        sys.exit(1)

    piece = board.piece_at(sq)
    if piece is None:
        white_attackers = get_legal_attackers(board, sq, chess.WHITE)
        black_attackers = get_legal_attackers(board, sq, chess.BLACK)
        data = {
            "square": square_str,
            "piece": None,
            "white_attackers": [piece_at_label(board, a) for a in white_attackers],
            "black_attackers": [piece_at_label(board, a) for a in black_attackers],
        }
        if as_json:
            return data
        wa = ", ".join(data["white_attackers"]) or "none"
        ba = ", ".join(data["black_attackers"]) or "none"
        return f"{square_str}: empty\n  White attacks: {wa}\n  Black attacks: {ba}"

    color = piece.color
    enemy = not color
    attackers = get_legal_attackers(board, sq, enemy)
    defenders = get_defenders(board, sq, color)

    # Is it hanging? Attacked and not defended (or attacked by lower value)
    is_hanging = len(attackers) > 0 and len(defenders) == 0
    lowest_attacker_value = None
    if attackers:
        lowest_attacker_value = min(
            PIECE_VALUES.get(board.piece_at(a).piece_type, 0) for a in attackers if board.piece_at(a)
        )
    piece_value = PIECE_VALUES[piece.piece_type]
    can_be_taken_by_lower = (
        lowest_attacker_value is not None and lowest_attacker_value < piece_value
    )

    data = {
        "square": square_str,
        "piece": piece_label(piece),
        "value": piece_value,
        "attackers": [piece_at_label(board, a) for a in attackers],
        "defenders": [piece_at_label(board, d) for d in defenders],
        "is_hanging": is_hanging,
        "can_be_taken_by_lower": can_be_taken_by_lower,
    }

    if as_json:
        return data

    atk = ", ".join(data["attackers"]) or "none"
    dfn = ", ".join(data["defenders"]) or "none"
    lines = [f"{square_str}: {piece_label(piece)} (value: {piece_value})"]
    lines.append(f"  Attacked by: {atk}")
    lines.append(f"  Defended by: {dfn}")
    if is_hanging:
        lines.append("  *** HANGING (attacked, no defenders) ***")
    if can_be_taken_by_lower:
        lines.append(f"  *** Can be captured by lower-value piece (attacker value: {lowest_attacker_value}) ***")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claim verification — check proposer's claimed LINE and WHITE_THREAT
# ---------------------------------------------------------------------------

def verify_claimed_line(board: chess.Board, candidate_move_str: str, line_tokens: list[str]) -> dict:
    """
    Verify the proposer's claimed line.
    Rules:
    - First ply must normalize to the proposed move
    - All plies must be legal in sequence
    - If legal, compute material outcome at the end
    Returns dict with: valid, hard_failures, warnings, material_outcome, resulting_fen
    """
    hard_failures = []
    warnings = []

    if not line_tokens:
        return {"valid": True, "hard_failures": [], "warnings": ["No LINE provided."],
                "material_outcome": None, "resulting_fen": None}

    sim = board.copy()
    candidate_move = parse_move(board, candidate_move_str)
    if candidate_move is None:
        return {"valid": False, "hard_failures": ["Candidate move itself is illegal."],
                "warnings": [], "material_outcome": None, "resulting_fen": None}

    # Check first ply matches the candidate
    first_move = parse_move(sim, line_tokens[0])
    if first_move is None:
        hard_failures.append(f"LINE ply 1 '{line_tokens[0]}' is not a legal move.")
        return {"valid": False, "hard_failures": hard_failures, "warnings": [],
                "material_outcome": None, "resulting_fen": None}

    if first_move != candidate_move:
        # Try normalizing both to UCI
        if first_move.uci() != candidate_move.uci():
            hard_failures.append(
                f"LINE ply 1 '{line_tokens[0]}' does not match candidate move "
                f"'{candidate_move_str}' (got {first_move.uci()} vs {candidate_move.uci()})."
            )
            return {"valid": False, "hard_failures": hard_failures, "warnings": [],
                    "material_outcome": None, "resulting_fen": None}

    # Simulate all plies
    mat_before = material_balance(board, board.turn)
    for i, token in enumerate(line_tokens):
        move = parse_move(sim, token)
        if move is None:
            hard_failures.append(f"LINE ply {i+1} '{token}' is illegal at position {sim.fen()}.")
            return {"valid": False, "hard_failures": hard_failures, "warnings": warnings,
                    "material_outcome": None, "resulting_fen": sim.fen()}
        sim.push(move)

    mat_after = material_balance(sim, board.turn)
    material_outcome = mat_after - mat_before

    if material_outcome <= -2:
        hard_failures.append(
            f"Claimed LINE loses material (delta: {material_outcome}). "
            "The proposer's own projected continuation is self-defeating."
        )

    return {
        "valid": True,
        "hard_failures": hard_failures,
        "warnings": warnings,
        "material_outcome": material_outcome,
        "resulting_fen": sim.fen(),
    }


def verify_white_threat(board_after_move: chess.Board, threat_token: str) -> dict:
    """
    Verify the proposer's claimed WHITE_THREAT.
    Rules:
    - Must normalize to a legal White reply from the post-move position
    - If legal, simulate it and evaluate whether it creates a tactical problem
    - If it's a real strong threat, report it; if harmless, flag as claim mismatch
    Returns dict with: valid, is_real_threat, hard_failures, warnings, explanation
    """
    hard_failures = []
    warnings = []

    if not threat_token or threat_token.lower() in ("none", "-", "n/a"):
        return {"valid": True, "is_real_threat": False, "hard_failures": [],
                "warnings": ["No WHITE_THREAT claimed."], "explanation": "No threat claimed."}

    # Must be White's turn in the post-move position
    if board_after_move.turn != chess.WHITE:
        return {"valid": False, "is_real_threat": False,
                "hard_failures": ["WHITE_THREAT check: not White's turn in post-move position."],
                "warnings": [], "explanation": "Internal error: wrong turn."}

    threat_move = parse_move(board_after_move, threat_token)
    if threat_move is None:
        hard_failures.append(f"WHITE_THREAT '{threat_token}' is not a legal White move.")
        return {"valid": False, "is_real_threat": False, "hard_failures": hard_failures,
                "warnings": [], "explanation": f"'{threat_token}' is illegal."}

    # Simulate the threat
    mover_color = not board_after_move.turn  # Black (we're checking White's threat against Black)
    pre_threat = material_balance(board_after_move, mover_color)

    threat_board = board_after_move.copy()
    threat_san = board_after_move.san(threat_move)
    threat_board.push(threat_move)

    is_check = threat_board.is_check()
    is_mate = threat_board.is_checkmate()
    is_capture = board_after_move.is_capture(threat_move)

    # Evaluate severity using existing helpers
    post_threat = worst_capture_balance_after_response(threat_board, mover_color)
    material_loss = pre_threat - post_threat

    is_real_threat = False
    explanation = f"{threat_san} is legal."

    if is_mate:
        is_real_threat = True
        explanation = f"{threat_san} is CHECKMATE — this is a critical threat."
    elif material_loss >= 2:
        is_real_threat = True
        explanation = f"{threat_san} forces material loss of {material_loss} for Black."
    elif is_check:
        # Check that doesn't lose material — moderate threat
        is_real_threat = True
        explanation = f"{threat_san} gives check."
    elif is_capture:
        captured = board_after_move.piece_at(threat_move.to_square)
        cap_val = PIECE_VALUES.get(captured.piece_type, 0) if captured else 0
        if cap_val >= 3:
            is_real_threat = True
            explanation = f"{threat_san} captures a {PIECE_NAMES.get(captured.piece_type, 'piece')}."
        else:
            explanation = f"{threat_san} captures a pawn — minor threat."
    else:
        # Quiet move — check if it creates tactical problems
        if material_loss >= 1:
            is_real_threat = True
            explanation = f"{threat_san} creates tactical pressure (material risk: {material_loss})."
        else:
            warnings.append(f"Claimed WHITE_THREAT '{threat_san}' appears harmless.")
            explanation = f"{threat_san} does not create an immediate tactical problem."

    return {
        "valid": True,
        "is_real_threat": is_real_threat,
        "hard_failures": hard_failures,
        "warnings": warnings,
        "explanation": explanation,
        "threat_san": threat_san,
        "material_loss": material_loss,
    }


def cmd_validate(board: chess.Board, move_str: str, as_json: bool = False) -> str | dict:
    """Validate a candidate move against deterministic tactical guardrails."""
    move = parse_move(board, move_str)
    if move is None:
        data = {
            "input": move_str,
            "legal": False,
            "passed": False,
            "uci": None,
            "san": None,
            "resulting_fen": board.fen(),
            "moved_piece": None,
            "destination": None,
            "moved_piece_attackers": [],
            "moved_piece_defenders": [],
            "opponent_checks": [],
            "opponent_captures": [],
            "quiet_hostile_replies": [],
            "hard_failures": ["Illegal or unparseable move."],
            "warnings": [],
            "explanation": f"{move_str} is illegal or could not be parsed.",
        }
        if as_json:
            return data
        return (
            f"Move: {move_str}\n"
            "Status: FAIL\n"
            "Hard failures:\n"
            "  - Illegal or unparseable move.\n"
            f"Summary: {data['explanation']}"
        )

    mover_color = board.turn
    enemy_color = not mover_color
    mover_name = "white" if mover_color == chess.WHITE else "black"
    enemy_name = "White" if enemy_color == chess.WHITE else "Black"
    san = board.san(move)
    uci = move.uci()
    moving_piece_before = board.piece_at(move.from_square)
    capture_label, capture_value = capture_info(board, move)

    post_move = board.copy()
    post_move.push(move)

    moved_piece_after = post_move.piece_at(move.to_square)
    destination = square_name(move.to_square)
    moved_piece_attackers = (
        [piece_at_label(post_move, a) for a in get_legal_attackers(post_move, move.to_square, enemy_color)]
        if moved_piece_after else []
    )
    moved_piece_defenders = (
        [piece_at_label(post_move, d) for d in get_defenders(post_move, move.to_square, mover_color)]
        if moved_piece_after else []
    )

    baseline_balance = material_balance(post_move, mover_color)
    baseline_hanging = {
        piece["square"]: piece for piece in collect_hanging_pieces(post_move, mover_color, min_value=3)
    }

    opponent_checks = []
    opponent_captures = []
    quiet_hostile_replies = []
    hard_failures: list[str] = []
    warnings: list[str] = []
    _hard_fail_replies: set[str] = set()  # track which replies already generated hard failures

    for reply in list(post_move.legal_moves):
        reply_san = post_move.san(reply)
        reply_uci = reply.uci()
        reply_capture_label, reply_capture_value = capture_info(post_move, reply)

        reply_board = post_move.copy()
        reply_board.push(reply)
        is_check = reply_board.is_check()
        is_checkmate = reply_board.is_checkmate()

        if is_check:
            opponent_checks.append({"san": reply_san, "uci": reply_uci, "checkmate": is_checkmate})
        if is_check and not is_checkmate:
            warnings.append(f"{reply_san} gives check.")
            pre_check_baseline = worst_capture_balance_after_response(post_move, mover_color)
            best_evasion_balance = best_evasion_balance_after_check(reply_board, mover_color)
            new_material_loss = pre_check_baseline - best_evasion_balance
            if new_material_loss >= 2:
                hard_failures.append(
                    f"{reply_san} check — every evasion loses material beyond baseline "
                    f"(best case: -{new_material_loss}). Likely fork or tactical shot."
                )
        if is_checkmate:
            hard_failures.append(f"{reply_san} is immediate checkmate for {enemy_name}.")

        # Checks are fully handled by check-evasion analysis above.
        # Skip quiet hostile reply analysis for checks — it would double-count
        # and miss that Black can recapture/evade the checking piece.
        if is_check:
            continue

        if reply_capture_label:
            free_win = best_immediate_recapture_balance(reply_board, mover_color) < baseline_balance
            opponent_captures.append({
                "san": reply_san,
                "uci": reply_uci,
                "captured_piece": reply_capture_label,
                "captured_value": reply_capture_value,
                "free_win": free_win,
            })
            if free_win:
                message = f"{reply_san} wins {reply_capture_label} with no immediate equalizing recapture."
                if reply_capture_value >= 3:
                    hard_failures.append(message)
                else:
                    warnings.append(message)
            continue

        # --- Quiet hostile reply analysis ---
        quiet_threats = []
        severity = 0
        safe_moves = []

        # (a) NEW attacks on the moved piece (ignore pre-existing attackers)
        moved_piece_still_present = reply_board.piece_at(move.to_square)
        if moved_piece_still_present and moved_piece_still_present.color == mover_color:
            pre_reply_attackers = set(get_legal_attackers(post_move, move.to_square, enemy_color))
            post_reply_attackers = set(get_legal_attackers(reply_board, move.to_square, enemy_color))
            new_attackers = post_reply_attackers - pre_reply_attackers
            if new_attackers:
                safe_moves = safe_moves_for_piece(reply_board, move.to_square, mover_color)
                quiet_threats.append(f"attacks the moved piece on {destination}")
                severity += 3
                if moved_piece_still_present.piece_type not in (chess.PAWN, chess.KING) and not safe_moves:
                    warnings.append(
                        f"{reply_san} traps the moved piece on {destination} with 0 safe legal moves."
                    )
                else:
                    warnings.append(f"{reply_san} attacks the moved piece on {destination}.")

        # (b) Non-check fork detection: reply piece attacks 2+ valuable Black pieces
        reply_piece = reply_board.piece_at(reply.to_square)
        if reply_piece and reply_piece.color == enemy_color:
            forked_pieces = []
            for sq in reply_board.attacks(reply.to_square):
                target = reply_board.piece_at(sq)
                if target and target.color == mover_color:
                    target_val = PIECE_VALUES.get(target.piece_type, 0)
                    is_king = target.piece_type == chess.KING
                    if is_king or target_val >= 3:
                        # Check if target is undefended or under-defended
                        defenders = get_defenders(reply_board, sq, mover_color)
                        forked_pieces.append({
                            "piece": piece_at_label(reply_board, sq),
                            "value": target_val,
                            "is_king": is_king,
                            "defended": len(defenders) > 0,
                        })
            if len(forked_pieces) >= 2:
                # King + major/minor fork
                forker_capturable = can_capture_reply_piece_safely(
                    reply_board, reply.to_square, mover_color
                )
                has_king = any(p["is_king"] for p in forked_pieces)
                total_value = sum(p["value"] for p in forked_pieces if not p["is_king"])
                fork_desc = ", ".join(p["piece"] for p in forked_pieces)
                quiet_threats.append(f"forks {fork_desc}")
                severity += total_value
                if forker_capturable:
                    warnings.append(
                        f"{reply_san} appears to fork {fork_desc}, but the forking piece can be captured immediately."
                    )
                elif has_king and total_value >= 3:
                    warnings.append(
                        f"{reply_san} forks {fork_desc}. King must respond, so follow-up loss is possible."
                    )
                elif total_value >= 5 and any(not p["defended"] for p in forked_pieces if not p["is_king"]):
                    warnings.append(
                        f"{reply_san} forks {fork_desc} with undefended target(s)."
                    )
                else:
                    warnings.append(f"{reply_san} forks {fork_desc}.")

        # (c) Net material evaluation after quiet reply (2-ply: opponent quiet move → our best response)
        # Only run for replies with severity > 0 or that are "interesting" (attacks, forks)
        if not is_check and not reply_capture_label and quiet_threats:
            post_reply_balance = worst_capture_balance_after_response(reply_board, mover_color)
            pre_reply_baseline = worst_capture_balance_after_response(post_move, mover_color)
            net_loss = pre_reply_baseline - post_reply_balance
            if net_loss >= 2 and reply_san not in _hard_fail_replies:
                _hard_fail_replies.add(reply_san)
                hard_failures.append(
                    f"{reply_san} forces net material loss of {net_loss} beyond baseline."
                )
                severity += net_loss

        # (d) New hanging pieces after the reply
        new_hanging = []
        for piece in collect_hanging_pieces(reply_board, mover_color, min_value=3):
            if piece["square"] not in baseline_hanging:
                new_hanging.append(piece)
        if new_hanging:
            squares = ", ".join(f"{piece['piece']} on {piece['square']}" for piece in new_hanging)
            max_hanging_value = max(piece["value"] for piece in new_hanging)
            quiet_threats.append(f"creates a new hanging {mover_name} piece: {squares}")
            severity += max_hanging_value

            # Check if the hanging piece can be saved:
            # 1. Can the mover move the hanging piece to safety?
            # 2. If not, after opponent captures it, can the mover recapture equally?
            is_free_loss = False
            for h_piece in new_hanging:
                h_sq = chess.parse_square(h_piece["square"])
                # Can the piece escape? (mover's turn — check if it has safe moves)
                escape_moves = safe_moves_for_piece(reply_board, h_sq, mover_color)
                if escape_moves:
                    continue  # piece can escape — not a free loss

                # Piece can't escape. After mover passes, can opponent capture it freely?
                # Simulate: mover does something else, opponent captures the hanging piece
                # Use worst_capture_balance: if opponent capturing gives a net loss, it's free
                # But we're at mover's turn. We need to check: on opponent's NEXT turn,
                # does capturing the hanging piece win material?
                # Simpler: the piece is hanging (attacked, no defenders, can't escape).
                # If opponent captures it, mover needs a recapture on that square.
                # Check: after opponent takes, can mover recapture?
                capturers = get_legal_attackers(reply_board, h_sq, enemy_color)
                if capturers:
                    # Check ALL captures of the hanging piece — any free win means it's lost
                    hypo = reply_board.copy()
                    hypo.turn = enemy_color
                    baseline_bal = material_balance(reply_board, mover_color)
                    for cap_move in hypo.legal_moves:
                        if cap_move.to_square == h_sq and hypo.is_capture(cap_move):
                            cap_board = hypo.copy()
                            cap_board.push(cap_move)
                            recapture_bal = best_immediate_recapture_balance(cap_board, mover_color)
                            if recapture_bal < baseline_bal:
                                is_free_loss = True
                                break  # found a free win — no need to check more

            if is_free_loss and max_hanging_value >= 3:
                hard_failures.append(
                    f"{reply_san} creates a freely capturable {mover_name} piece worth {max_hanging_value}: {squares}."
                )
            elif is_free_loss:
                warnings.append(f"{reply_san} creates a freely capturable {mover_name} piece: {squares}.")
            else:
                warnings.append(f"{reply_san} creates a new hanging {mover_name} piece: {squares}.")

        if quiet_threats:
            quiet_hostile_replies.append({
                "san": reply_san,
                "uci": reply_uci,
                "threats": quiet_threats,
                "safe_moves": safe_moves,
                "severity": severity,
            })

    def dedupe(items: list[str]) -> list[str]:
        seen = set()
        unique = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique

    hard_failures = dedupe(hard_failures)
    warnings = dedupe(warnings)
    opponent_checks.sort(key=lambda item: item["checkmate"], reverse=True)
    opponent_captures.sort(key=lambda item: (item["free_win"], item["captured_value"]), reverse=True)
    quiet_hostile_replies.sort(key=lambda item: item["severity"], reverse=True)

    explanation = (
        hard_failures[0]
        if hard_failures else (
            warnings[0]
            if warnings else f"{san} passes validation."
        )
    )

    data = {
        "input": move_str,
        "legal": True,
        "passed": not hard_failures,
        "uci": uci,
        "san": san,
        "capture": capture_label,
        "capture_value": capture_value,
        "resulting_fen": post_move.fen(),
        "moved_piece": piece_label(moved_piece_after) if moved_piece_after else None,
        "destination": destination,
        "moved_piece_attackers": moved_piece_attackers,
        "moved_piece_defenders": moved_piece_defenders,
        "opponent_checks": opponent_checks[:5],
        "opponent_captures": opponent_captures[:5],
        "quiet_hostile_replies": quiet_hostile_replies[:5],
        "hard_failures": hard_failures,
        "warnings": warnings,
        "explanation": explanation,
        "material_balance_after_move": baseline_balance,
        "moving_piece_from": square_name(move.from_square),
        "moving_piece_before": piece_label(moving_piece_before) if moving_piece_before else None,
    }

    if as_json:
        return data

    lines = [
        f"Move: {san} ({uci})",
        f"Status: {'PASS' if data['passed'] else 'FAIL'}",
        f"Moved piece: {data['moved_piece'] or 'none'} to {destination}",
        f"After the move: attacked by {', '.join(moved_piece_attackers) or 'none'} | defended by {', '.join(moved_piece_defenders) or 'none'}",
    ]
    if capture_label:
        lines.append(f"Capture: {capture_label} (value {capture_value})")
    if data["opponent_checks"]:
        lines.append(
            "Opponent checks: " + ", ".join(
                f"{item['san']}{'#' if item['checkmate'] else ''}" for item in data["opponent_checks"]
            )
        )
    if data["opponent_captures"]:
        lines.append(
            "Opponent captures: " + ", ".join(
                f"{item['san']} [{item['captured_piece']}{', free' if item['free_win'] else ''}]"
                for item in data["opponent_captures"]
            )
        )
    if data["quiet_hostile_replies"]:
        lines.append(
            "Quiet hostile replies: " + ", ".join(
                f"{item['san']} ({'; '.join(item['threats'])})"
                for item in data["quiet_hostile_replies"]
            )
        )
    if hard_failures:
        lines.append("Hard failures:")
        for failure in hard_failures:
            lines.append(f"  - {failure}")
    if warnings:
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"  - {warning}")
    lines.append(f"Summary: {explanation}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Candidates — pre-computed move consequences for LLM evaluation
# ---------------------------------------------------------------------------

def _compute_one_candidate(board: chess.Board, move: chess.Move) -> dict:
    """Compute all facts about one candidate move. Board is NOT modified."""
    san = board.san(move)
    uci = move.uci()
    mover = board.piece_at(move.from_square)
    mover_value = PIECE_VALUES.get(mover.piece_type, 0) if mover else 0
    mover_color = mover.color if mover else board.turn
    enemy_color = not mover_color

    # Capture info (handle en passant)
    is_capture = board.is_capture(move)
    captured_value = 0
    captured_piece = None
    if is_capture:
        if board.is_en_passant(move):
            captured_piece = "pawn"
            captured_value = 1
        else:
            target = board.piece_at(move.to_square)
            if target:
                captured_piece = PIECE_NAMES[target.piece_type]
                captured_value = PIECE_VALUES[target.piece_type]

    # Material before
    mat_before = material_count(board, chess.WHITE) - material_count(board, chess.BLACK)

    # Push move to analyze consequences
    board_after = board.copy()
    board_after.push(move)

    is_check = board_after.is_check()
    is_checkmate = board_after.is_checkmate()

    # Material after (from white's perspective, delta for the moving side)
    mat_after = material_count(board_after, chess.WHITE) - material_count(board_after, chess.BLACK)
    mat_delta = mat_after - mat_before
    if mover_color == chess.BLACK:
        mat_delta = -mat_delta  # positive = good for the moving side

    # Piece safety on destination square
    moved_sq = move.to_square
    attackers = get_legal_attackers(board_after, moved_sq, enemy_color)
    defenders = get_defenders(board_after, moved_sq, mover_color)
    atk_count = len(attackers)
    def_count = len(defenders)
    is_safe = atk_count == 0 or def_count >= atk_count

    # New attacks: enemy pieces attacked by moved piece in new position
    new_attacks = []
    for sq in board_after.attacks(moved_sq):
        target = board_after.piece_at(sq)
        if target and target.color == enemy_color:
            new_attacks.append({
                "piece": piece_at_label(board_after, sq),
                "value": PIECE_VALUES.get(target.piece_type, 0),
            })

    # Safe retreat squares for the moved piece
    safe_squares = 0
    retreat_squares = []
    if mover and mover.piece_type != chess.PAWN:
        # Set it to the mover's turn to check retreats
        hypo = board_after.copy()
        hypo.turn = mover_color
        for rm in hypo.legal_moves:
            if rm.from_square == moved_sq:
                rsq = rm.to_square
                # Is the retreat square attacked by enemy?
                enemy_attackers = get_legal_attackers(board_after, rsq, enemy_color)
                if not enemy_attackers:
                    safe_squares += 1
                retreat_squares.append(square_name(rsq))

    # Opponent forcing replies: captures, checks, AND pawn advances threatening the piece
    opp_captures = []
    opp_checks = []
    opp_pawn_threats = []
    for opp_move in board_after.legal_moves:
        opp_san = board_after.san(opp_move)
        if board_after.is_capture(opp_move):
            opp_target = board_after.piece_at(opp_move.to_square)
            val = PIECE_VALUES.get(opp_target.piece_type, 0) if opp_target else 0
            opp_captures.append({"san": opp_san, "value": val})
        board_after.push(opp_move)
        if board_after.is_check():
            board_after.pop()
            if opp_san not in [c["san"] for c in opp_captures]:
                opp_checks.append(opp_san)
        else:
            board_after.pop()

    # Pawn advances that attack the moved piece or its retreat squares
    if mover and mover.piece_type != chess.PAWN:
        for opp_move in board_after.legal_moves:
            opp_piece = board_after.piece_at(opp_move.from_square)
            if opp_piece and opp_piece.piece_type == chess.PAWN:
                # Does this pawn advance attack the moved piece's square or a retreat square?
                opp_board = board_after.copy()
                opp_board.push(opp_move)
                targets = set(retreat_squares + [square_name(moved_sq)])
                pawn_attacks = {square_name(sq) for sq in opp_board.attacks(opp_move.to_square)}
                threatened = targets & pawn_attacks
                if threatened:
                    opp_pawn_threats.append({
                        "san": board_after.san(opp_move),
                        "threatens": list(threatened),
                    })

    # Sort opponent captures by value descending, cap
    opp_captures.sort(key=lambda x: x["value"], reverse=True)
    opp_captures = opp_captures[:5]
    opp_checks = opp_checks[:3]
    opp_pawn_threats = opp_pawn_threats[:3]

    # Trap warning — only fire when genuinely dangerous, not on every pawn advance
    trap_warning = False
    if mover and mover.piece_type != chess.PAWN:
        if atk_count > 0 and safe_squares == 0:
            trap_warning = True  # attacked with no escape
        elif safe_squares <= 1 and any(
            square_name(moved_sq) in t.get("threatens", []) for t in opp_pawn_threats
        ):
            trap_warning = True  # few escapes AND pawn threatens the piece itself

    # Development flag
    is_development = False
    if mover:
        from_rank = chess.square_rank(move.from_square)
        back_rank = 0 if mover_color == chess.WHITE else 7
        if from_rank == back_rank and mover.piece_type in [chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN]:
            is_development = True

    # Pawn structure note
    pawn_note = None
    if mover and mover.piece_type == chess.PAWN:
        ps_before = pawn_structure(board, mover_color)
        ps_after = pawn_structure(board_after, mover_color)
        new_isolated = set(ps_after["isolated"]) - set(ps_before["isolated"])
        new_doubled = set(ps_after["doubled"]) - set(ps_before["doubled"])
        new_passed = set(ps_after["passed"]) - set(ps_before["passed"])
        notes = []
        if new_isolated:
            notes.append(f"creates isolated {','.join(new_isolated)}")
        if new_doubled:
            notes.append(f"creates doubled {','.join(new_doubled)}")
        if new_passed:
            notes.append(f"creates passed {','.join(new_passed)}")
        pawn_note = "; ".join(notes) if notes else None

    return {
        "san": san,
        "uci": uci,
        "is_capture": is_capture,
        "captured_piece": captured_piece,
        "captured_value": captured_value,
        "is_check": is_check,
        "is_checkmate": is_checkmate,
        "mat_delta": mat_delta,
        "piece_safety": {"attackers": atk_count, "defenders": def_count, "safe": is_safe},
        "new_attacks": new_attacks[:5],
        "safe_squares": safe_squares,
        "opp_captures": [c["san"] for c in opp_captures],
        "opp_checks": opp_checks,
        "opp_pawn_threats": opp_pawn_threats,
        "trap_warning": trap_warning,
        "is_development": is_development,
        "pawn_note": pawn_note,
    }


def _priority_score(c: dict, board: chess.Board) -> int:
    """Score a candidate for pre-filtering. Higher = more interesting."""
    score = 0
    if c["is_checkmate"]:
        return 1000
    if c["is_check"]:
        score += 100
    if c["is_capture"]:
        mover = board.piece_at(chess.parse_square(c["uci"][:2]))
        mover_val = PIECE_VALUES.get(mover.piece_type, 0) if mover else 0
        if c["captured_value"] >= mover_val:
            score += 50 + c["captured_value"]
        elif not c["piece_safety"]["safe"]:
            score += 30
        else:
            score += 20 + c["captured_value"]
    if c["new_attacks"]:
        best_target = max(a["value"] for a in c["new_attacks"])
        score += 10 + best_target
    if c["is_development"]:
        score += 5
    if c["uci"] in ("e1g1", "e1c1", "e8g8", "e8c8"):  # castling
        score += 10
    # Pawn to 7th rank bonus — only for pawns
    mover_piece = board.piece_at(chess.parse_square(c["uci"][:2]))
    if mover_piece and mover_piece.piece_type == chess.PAWN:
        to_rank = int(c["uci"][3])
        if board.turn == chess.WHITE and to_rank == 7:
            score += 25
        elif board.turn == chess.BLACK and to_rank == 2:
            score += 25
    return score


def cmd_candidates(board: chess.Board, max_candidates: int = 10, as_json: bool = False) -> str | dict:
    """Pre-compute consequences for all legal moves. Returns ranked candidates."""
    phase = detect_game_phase(board)
    w_mat = material_count(board, chess.WHITE)
    b_mat = material_count(board, chess.BLACK)
    turn = "white" if board.turn == chess.WHITE else "black"

    # Compute position context: hanging pieces, pieces under attack, threats
    my_color = board.turn
    enemy_color = not my_color
    hanging = []
    under_attack = []
    enemy_hanging = []

    for pt in [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN]:
        for sq in board.pieces(pt, my_color):
            attackers = get_legal_attackers(board, sq, enemy_color)
            defenders = get_defenders(board, sq, my_color)
            if attackers:
                sq_name = square_name(sq)
                atk_labels = [piece_at_label(board, a) for a in attackers]
                def_labels = [piece_at_label(board, d) for d in defenders]
                val = PIECE_VALUES[pt]
                if not defenders:
                    hanging.append({"piece": piece_at_label(board, sq), "square": sq_name,
                                    "value": val, "attackers": atk_labels})
                else:
                    under_attack.append({"piece": piece_at_label(board, sq), "square": sq_name,
                                         "value": val, "attackers": atk_labels, "defenders": def_labels})

        for sq in board.pieces(pt, enemy_color):
            attackers = get_legal_attackers(board, sq, my_color)
            defenders = get_defenders(board, sq, enemy_color)
            if attackers and not defenders:
                enemy_hanging.append({"piece": piece_at_label(board, sq), "square": square_name(sq),
                                      "value": PIECE_VALUES[pt]})

    position_summary = {
        "fen": board.fen(),
        "turn": turn,
        "phase": phase,
        "material_white": w_mat,
        "material_black": b_mat,
        "material_diff": w_mat - b_mat,
        "in_check": board.is_check(),
        "my_hanging": hanging,
        "my_under_attack": under_attack,
        "enemy_hanging": enemy_hanging,
    }

    # Compute all candidates
    all_candidates = []
    for move in board.legal_moves:
        c = _compute_one_candidate(board, move)
        c["priority"] = _priority_score(c, board)
        all_candidates.append(c)

    # Sort by priority
    all_candidates.sort(key=lambda x: x["priority"], reverse=True)

    # If in check, include all (usually 1-5)
    if board.is_check():
        top = all_candidates
    else:
        top = all_candidates[:max_candidates]
        # Ensure at least 3
        if len(top) < 3 and len(all_candidates) > len(top):
            top = all_candidates[:3]

    data = {
        "position_summary": position_summary,
        "all_ranked": all_candidates,
        "top": top,
    }

    if as_json:
        return data

    # Natural language table
    lines = []
    ps = position_summary
    mat_desc = f"W{ps['material_white']} B{ps['material_black']}"
    if ps["material_diff"] > 0:
        mat_desc += f" (White +{ps['material_diff']})"
    elif ps["material_diff"] < 0:
        mat_desc += f" (Black +{-ps['material_diff']})"
    else:
        mat_desc += " (equal)"
    lines.append(f"POSITION: {ps['turn'].capitalize()} to move | {ps['phase'].capitalize()} | Material: {mat_desc}")

    # Urgent threats
    if ps["my_hanging"]:
        lines.append("")
        lines.append("!! YOUR HANGING PIECES (undefended + attacked — SAVE THEM):")
        for h in ps["my_hanging"]:
            lines.append(f"  {h['piece']} on {h['square']} (value {h['value']}) attacked by {', '.join(h['attackers'])}")
    if ps["my_under_attack"]:
        pieces_at_risk = [a for a in ps["my_under_attack"] if a["value"] >= 3]
        if pieces_at_risk:
            lines.append("")
            lines.append("PIECES UNDER ATTACK (defended but targeted):")
            for a in pieces_at_risk:
                lines.append(f"  {a['piece']} on {a['square']} attacked by {', '.join(a['attackers'])}, defended by {', '.join(a['defenders'])}")
    if ps["enemy_hanging"]:
        lines.append("")
        lines.append("OPPONENT HANGING PIECES (free to capture):")
        for h in ps["enemy_hanging"]:
            lines.append(f"  {h['piece']} on {h['square']} (value {h['value']})")
    if ps["in_check"]:
        lines.append("*** IN CHECK ***")
    lines.append("")
    lines.append(f"CANDIDATES ({len(top)}):")
    lines.append(f"{'#':<3} {'Move':<8} {'Type':<14} {'MatD':<6} {'Safety':<16} {'Attacks':<22} {'OppForcing'}")
    lines.append("-" * 100)

    for i, c in enumerate(top):
        # Type
        if c["is_checkmate"]:
            ctype = "CHECKMATE"
        elif c["is_check"]:
            ctype = "check"
        elif c["is_capture"]:
            ctype = f"capt({c['captured_piece'][:1]},{c['captured_value']})"
        elif c["is_development"]:
            ctype = "develop"
        elif c["uci"] in ("e1g1", "e1c1", "e8g8", "e8c8"):
            ctype = "castle"
        else:
            ctype = ""

        # MatDelta
        md = f"+{c['mat_delta']}" if c["mat_delta"] > 0 else str(c["mat_delta"])

        # Safety
        ps_info = c["piece_safety"]
        if ps_info["safe"]:
            safety = f"safe({ps_info['attackers']}atk,{ps_info['defenders']}def)"
        else:
            safety = f"RISK({ps_info['attackers']}atk,{ps_info['defenders']}def)"

        # Attacks
        attacks = ",".join(f"{a['piece']}({a['value']})" for a in c["new_attacks"][:3]) or "-"

        # OppForcing
        opp_parts = []
        if c["opp_captures"]:
            opp_parts.append(f"capt:{','.join(c['opp_captures'][:3])}")
        if c["opp_checks"]:
            opp_parts.append(f"chk:{','.join(c['opp_checks'][:2])}")
        if c["opp_pawn_threats"]:
            threats = ",".join(t["san"] for t in c["opp_pawn_threats"][:2])
            opp_parts.append(f"trap:{threats}")
        opp = " ".join(opp_parts) or "-"

        # Trap warning
        warn = " !!TRAP" if c["trap_warning"] else ""
        # Pawn note
        pnote = f" [{c['pawn_note']}]" if c.get("pawn_note") else ""

        lines.append(f"{i+1:<3} {c['san']:<8} {ctype:<14} {md:<6} {safety:<16} {attacks:<22} {opp}{warn}{pnote}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AgentChess Perception Layer — ground-truth board facts via python-chess"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # state
    p_state = subparsers.add_parser("state", help="Full board state description")
    p_state.add_argument("--fen", required=True, help="FEN string (quote it)")
    p_state.add_argument("--json", action="store_true", help="Output as JSON")

    # legal
    p_legal = subparsers.add_parser("legal", help="List all legal moves")
    p_legal.add_argument("--fen", required=True, help="FEN string")
    p_legal.add_argument("--json", action="store_true", help="Output as JSON")

    # simulate
    p_sim = subparsers.add_parser("simulate", help="Simulate a sequence of moves")
    p_sim.add_argument("--fen", required=True, help="FEN string")
    p_sim.add_argument("--move", action="append", required=True, dest="moves",
                        help="Move in SAN or UCI (repeat for multiple)")
    p_sim.add_argument("--json", action="store_true", help="Output as JSON")

    # query
    p_query = subparsers.add_parser("query", help="Query info about a square")
    p_query.add_argument("--fen", required=True, help="FEN string")
    p_query.add_argument("--square", required=True, help="Square to query (e.g. e4)")
    p_query.add_argument("--json", action="store_true", help="Output as JSON")

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate a candidate move")
    p_validate.add_argument("--fen", required=True, help="FEN string")
    p_validate.add_argument("--move", required=True, help="Move in SAN or UCI")
    p_validate.add_argument("--json", action="store_true", help="Output as JSON")

    # candidates
    p_cand = subparsers.add_parser("candidates", help="Pre-compute candidate move consequences")
    p_cand.add_argument("--fen", required=True, help="FEN string")
    p_cand.add_argument("--max", type=int, default=10, dest="max_candidates", help="Max candidates (default 10)")
    p_cand.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    try:
        board = chess.Board(args.fen)
    except ValueError as e:
        print(f"Invalid FEN: {e}", file=sys.stderr)
        sys.exit(1)

    as_json = getattr(args, "json", False)

    if args.command == "state":
        result = cmd_state(board, as_json)
    elif args.command == "legal":
        result = cmd_legal(board, as_json)
    elif args.command == "simulate":
        result = cmd_simulate(board, args.moves, as_json)
    elif args.command == "query":
        result = cmd_query(board, args.square, as_json)
    elif args.command == "validate":
        result = cmd_validate(board, args.move, as_json)
    elif args.command == "candidates":
        result = cmd_candidates(board, args.max_candidates, as_json)
    else:
        parser.print_help()
        sys.exit(1)

    if isinstance(result, dict):
        print(json.dumps(result, indent=2))
    else:
        print(result)


if __name__ == "__main__":
    main()
