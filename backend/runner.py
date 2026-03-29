"""
AgentChess Runner — Proposer + Validator controller.

The LLM proposes three ranked candidate moves from a grounded board brief.
A deterministic validator filters strict tactical blunders, and a stronger
critic model makes the final choose/override decision when needed.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import chess
import httpx

from perception import (
    cmd_state,
    cmd_validate,
    verify_claimed_line,
    verify_white_threat,
)
from opening_book import get_book_move

BACKEND_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class GeminiProvider:
    def __init__(self, model: str = "gemini-2.5-flash", *, thinking_budget: int | None = None):
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            env_path = BACKEND_DIR.parent.parent / "axp1246" / "backend" / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("GEMINI_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found")

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.genai = genai
        self.thinking_budget = thinking_budget

    def call(self, prompt: str) -> str:
        config = self.genai.types.GenerateContentConfig(
            temperature=0.3,
        )
        if self.thinking_budget is not None:
            config.thinking_config = self.genai.types.ThinkingConfig(
                thinking_budget=self.thinking_budget,
            )
        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                self.genai.types.Content(
                    role="user",
                    parts=[self.genai.types.Part(text=prompt)],
                )
            ],
            config=config,
        )
        return self._extract_text(response)

    @staticmethod
    def _extract_text(response) -> str:
        text = getattr(response, "text", None)
        if text:
            return text.strip()

        chunks = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                if getattr(part, "text", None):
                    chunks.append(part.text)
        return "\n".join(chunks).strip()


class ClaudeProvider:
    def __init__(self, model: str = "sonnet"):
        self.model = model

    def call(self, prompt: str) -> str:
        cmd = ["claude", "-p", prompt, "--model", self.model]
        for attempt in range(2):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if result.returncode != 0:
                    print(f"  [claude error] {result.stderr[:200]}", file=sys.stderr)
                    if attempt == 0:
                        time.sleep(2)
                        continue
                    return ""
                output = result.stdout.strip()
                if output:
                    return output
                if attempt == 0:
                    time.sleep(2)
            except subprocess.TimeoutExpired:
                print("  [claude timeout]", file=sys.stderr)
                if attempt == 0:
                    time.sleep(2)
                    continue
                return ""
        return ""


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------

PROPOSER_PROMPT = """You are BLACK in a human-vs-AI chess game.

You are given a grounded board brief generated from deterministic board perception.
Use your chess knowledge: opening patterns, tactical motifs, king safety, development, pawn structure, and long-term plans.

BOARD BRIEF:
{board_brief}

Choose the best 3 moves for BLACK and rank them from best to third-best.

Rules:
- Use the grounded board brief only. Do not invent pieces or squares.
- Prefer strategically coherent moves, not just short-term tactics.
- Avoid moves that leave material hanging or walk into obvious tactical shots.
- WHITE_THREAT must be a single concrete White move you think is most dangerous after your move. Check the WHITE'S MOST DANGEROUS IDEAS section for guidance.
- LINE must contain at least 3 plies: your move, White's likely reply, your follow-up. All must be legal. Extra plies are allowed, but only the first 3 will be validated.
- If you are unsure about the UCI, the SAN still needs to be correct.
- Return exactly 3 ranked moves in the exact format below.

Format EXACTLY:
CANDIDATES:
1. MOVE: <uci> (SAN: <san>) | LINE: <black_move> <white_reply> <black_followup> | WHITE_THREAT: <white_move_san> | REASONING: <why this is best>
2. MOVE: <uci> (SAN: <san>) | LINE: <black_move> <white_reply> <black_followup> | WHITE_THREAT: <white_move_san> | REASONING: <why this is next>
3. MOVE: <uci> (SAN: <san>) | LINE: <black_move> <white_reply> <black_followup> | WHITE_THREAT: <white_move_san> | REASONING: <why this is third>
"""


CRITIC_PROMPT = """You are BLACK's final chooser.

{critic_mode}

BOARD BRIEF:
{board_brief}

CANDIDATE CONTEXT:
{candidates_detail}

Rules:
- Do not restart the whole analysis.
- Prefer a listed candidate if one is clearly best.
- Only override if the candidates miss a clearly stronger move.
- Be concrete: give one short line and one short reason.

Return EXACTLY:
DECISION: <CHOOSE|OVERRIDE> <san-or-uci>
LINE: <black_move> <white_reply> <black_followup>
REASONING: <short why>
"""


# ---------------------------------------------------------------------------
# Parsing / formatting
# ---------------------------------------------------------------------------

def parse_candidates(text: str) -> list[dict]:
    candidates = []
    seen = set()

    # Required: MOVE | LINE | WHITE_THREAT | REASONING
    claim_pattern = re.compile(
        r"^\s*(\d+)\.\s*MOVE:\s*(\S+)\s*\(SAN:\s*([^)]+)\)\s*\|\s*LINE:\s*(.*?)\s*\|\s*WHITE_THREAT:\s*(\S+)\s*\|\s*REASONING:\s*(.+)$",
        re.MULTILINE,
    )

    def add_candidate(move_token: str, san: str, reasoning: str,
                      line: str = "", white_threat: str = "") -> None:
        key = (move_token, san)
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "move_token": move_token,
            "san": san,
            "line": line,
            "white_threat": white_threat,
            "reasoning": reasoning,
        })

    for match in claim_pattern.finditer(text):
        add_candidate(
            match.group(2).strip(),
            match.group(3).strip(),
            match.group(6).strip(),
            match.group(4).strip(),
            match.group(5).strip(),
        )
    return candidates[:3]


def parse_move_token(board: chess.Board, token: str) -> chess.Move | None:
    token = (token or "").strip()
    if not token:
        return None
    try:
        move = chess.Move.from_uci(token)
        if move in board.legal_moves:
            return move
    except ValueError:
        pass
    try:
        return board.parse_san(token)
    except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
        return None


def parse_critic_reply(text: str) -> tuple[str | None, str]:
    """Legacy single-move parser (kept for compatibility)."""
    reply_match = re.search(r"WHITE'S BEST REPLY:\s*(.+)", text or "", re.MULTILINE)
    reasoning_match = re.search(r"REASONING:\s*(.+)", text or "", re.MULTILINE)
    reply = reply_match.group(1).strip() if reply_match else None
    reasoning = reasoning_match.group(1).strip() if reasoning_match else ""
    return reply, reasoning


def parse_critic_choice(text: str) -> dict:
    decision_match = re.search(r"DECISION:\s*(CHOOSE|OVERRIDE)\s+(.+)", text or "", re.MULTILINE)
    line_match = re.search(r"LINE:\s*(.+)", text or "", re.MULTILINE)
    reasoning_match = re.search(r"REASONING:\s*(.+)", text or "", re.MULTILINE)
    return {
        "decision": decision_match.group(1).strip() if decision_match else None,
        "move": decision_match.group(2).strip() if decision_match else None,
        "line": line_match.group(1).strip() if line_match else "",
        "reasoning": reasoning_match.group(1).strip() if reasoning_match else "",
    }


def piece_needs_tactical_detail(piece: dict) -> bool:
    if piece["piece"] == "king":
        return True
    if piece["attackers"]:
        return True
    if piece["piece"] in {"queen", "rook", "bishop", "knight"} and not piece["defenders"]:
        return True
    return False


def describe_piece_lines(title: str, pieces: list[dict]) -> list[str]:
    lines = [title]
    for piece in pieces:
        base = f"  {piece['piece'].capitalize()} {piece['square']}"
        if piece_needs_tactical_detail(piece):
            details = []
            attacked_by = ", ".join(piece["attackers"]) or "none"
            defended_by = ", ".join(piece["defenders"]) or "none"
            details.append(f"attacked by: {attacked_by}")
            details.append(f"defended by: {defended_by}")
            if "pawn_shelter" in piece:
                shelter = ", ".join(piece["pawn_shelter"]) or "none"
                details.append(f"pawn shelter: {shelter}")
            lines.append(base + " | " + " | ".join(details))
        else:
            lines.append(base)
    return lines


def find_hanging_pieces(pieces: list[dict], color_name: str) -> list[str]:
    hanging = []
    for piece in pieces:
        if piece["piece"] != "king" and piece["attackers"] and not piece["defenders"]:
            hanging.append(f"{color_name} {piece['piece']} on {piece['square']}")
    return hanging


def _compute_white_threats(board: chess.Board) -> list[str]:
    """
    Compute White's most dangerous ideas from the current position.
    Uses a hypothetical board where it's White's turn to find tactical threats.
    Returns a list of human-readable threat descriptions.
    """
    from perception import get_legal_attackers, get_defenders, PIECE_VALUES, PIECE_NAMES

    threats = []
    # Create hypothetical board where White moves
    hypo = board.copy()
    hypo.turn = chess.WHITE

    # White checks
    white_checks = []
    for move in hypo.legal_moves:
        hypo2 = hypo.copy()
        hypo2.push(move)
        if hypo2.is_check():
            san = hypo.san(move)
            is_capture = hypo.is_capture(move)
            is_mate = hypo2.is_checkmate()
            # What does this check also attack? (fork detection)
            attacked_black = []
            to_sq = move.to_square
            for sq in hypo2.attacks(to_sq):
                piece = hypo2.piece_at(sq)
                if piece and piece.color == chess.BLACK and piece.piece_type != chess.KING:
                    attacked_black.append(f"{PIECE_NAMES[piece.piece_type]} on {chess.square_name(sq)}")
            if is_mate:
                threats.append(f"{san} is CHECKMATE")
            elif attacked_black:
                threats.append(f"{san} check, also attacks {', '.join(attacked_black)}")
            elif is_capture:
                threats.append(f"{san} check with capture")
            else:
                white_checks.append(san)

    if white_checks and not any("check" in t.lower() for t in threats):
        threats.append(f"Checks available: {', '.join(white_checks[:3])}")

    # White captures of undefended Black pieces
    for move in hypo.legal_moves:
        if not hypo.is_capture(move):
            continue
        target = hypo.piece_at(move.to_square)
        if not target or target.color != chess.BLACK:
            continue
        target_val = PIECE_VALUES.get(target.piece_type, 0)
        if target_val < 3:
            continue  # skip pawn captures for brevity
        defenders = get_defenders(board, move.to_square, chess.BLACK)
        if not defenders:
            san = hypo.san(move)
            threats.append(f"{san} captures undefended {PIECE_NAMES[target.piece_type]}")

    # White knight jumps that attack 2+ Black pieces (fork potential)
    for move in hypo.legal_moves:
        mover = hypo.piece_at(move.from_square)
        if not mover or mover.piece_type != chess.KNIGHT:
            continue
        hypo2 = hypo.copy()
        hypo2.push(move)
        attacked = []
        for sq in hypo2.attacks(move.to_square):
            piece = hypo2.piece_at(sq)
            if piece and piece.color == chess.BLACK and PIECE_VALUES.get(piece.piece_type, 0) >= 3:
                attacked.append(f"{PIECE_NAMES[piece.piece_type]} on {chess.square_name(sq)}")
        if len(attacked) >= 2:
            san = hypo.san(move)
            threats.append(f"{san} knight fork on {', '.join(attacked)}")

    # Hanging Black pieces (undefended and capturable)
    from perception import collect_hanging_pieces
    hanging = collect_hanging_pieces(board, chess.BLACK, min_value=3)
    for h in hanging:
        threats.append(f"{h['piece']} on {h['square']} is undefended (captured by {', '.join(h['attackers'][:2])})")

    # Deduplicate and cap
    seen = set()
    unique = []
    for t in threats:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique[:6]


def find_undefended_high_value(pieces: list[dict], color_name: str) -> list[str]:
    values = {"queen", "rook", "bishop", "knight"}
    items = []
    for piece in pieces:
        if piece["piece"] in values and not piece["defenders"]:
            items.append(f"{color_name} {piece['piece']} on {piece['square']}")
    return items


def board_special_notes(board: chess.Board, state: dict) -> list[str]:
    notes = []
    if state.get("en_passant"):
        notes.append(f"En passant square is {state['en_passant']}.")

    promotion_moves = []
    for move in board.legal_moves:
        if move.promotion:
            promotion_moves.append(f"{board.san(move)} ({move.uci()})")
    if promotion_moves:
        notes.append("Promotion moves available: " + ", ".join(promotion_moves[:6]))
    return notes


def build_board_brief(board: chess.Board, move_history: list[str] | None = None) -> str:
    state = cmd_state(board, as_json=True)
    turn = state["turn"].capitalize()
    phase = state["phase"].capitalize()
    white = state["material"]["white"]
    black = state["material"]["black"]
    diff = state["material"]["diff"]
    if diff > 0:
        material_line = f"White {white} vs Black {black} (White +{diff})"
    elif diff < 0:
        material_line = f"White {white} vs Black {black} (Black +{-diff})"
    else:
        material_line = f"White {white} vs Black {black} (equal)"

    lines = [
        f"Turn: {turn} | Phase: {phase}",
        f"Material: {material_line}",
        "Castling: " + (", ".join(state["castling"]) if state["castling"] else "none"),
        f"Check status: {'in check' if state['in_check'] else 'not in check'}",
    ]
    if move_history:
        pairs = []
        for i in range(0, len(move_history), 2):
            n = i // 2 + 1
            w = move_history[i]
            b = move_history[i + 1] if i + 1 < len(move_history) else ""
            pairs.append(f"{n}. {w} {b}".strip())
        lines.append(f"MOVE HISTORY: {' '.join(pairs)}")

    lines.append("")
    lines.extend(describe_piece_lines("WHITE PIECES:", state["white_pieces"]))
    lines.append("")
    lines.extend(describe_piece_lines("BLACK PIECES:", state["black_pieces"]))

    lines.append("")
    lines.append("WHITE PAWNS:")
    lines.append(f"  Squares: {', '.join(state['white_pawns']['squares']) or 'none'}")
    lines.append(f"  Isolated: {', '.join(state['white_pawns']['isolated']) or 'none'}")
    lines.append(f"  Doubled: {', '.join(state['white_pawns']['doubled']) or 'none'}")
    lines.append(f"  Passed: {', '.join(state['white_pawns']['passed']) or 'none'}")
    lines.append(f"  Connected: {', '.join(state['white_pawns']['connected']) or 'none'}")
    lines.append(f"  Backward: {', '.join(state['white_pawns']['backward']) or 'none'}")

    lines.append("")
    lines.append("BLACK PAWNS:")
    lines.append(f"  Squares: {', '.join(state['black_pawns']['squares']) or 'none'}")
    lines.append(f"  Isolated: {', '.join(state['black_pawns']['isolated']) or 'none'}")
    lines.append(f"  Doubled: {', '.join(state['black_pawns']['doubled']) or 'none'}")
    lines.append(f"  Passed: {', '.join(state['black_pawns']['passed']) or 'none'}")
    lines.append(f"  Connected: {', '.join(state['black_pawns']['connected']) or 'none'}")
    lines.append(f"  Backward: {', '.join(state['black_pawns']['backward']) or 'none'}")

    lines.append("")
    lines.append("LEGAL TACTICAL MOVES:")
    lines.append(f"  Checks: {', '.join(state['checks']) or 'none'}")
    lines.append(f"  Captures: {', '.join(state['captures']) or 'none'}")
    lines.append(f"  Quiet moves: {len(state['other_moves'])}")

    black_hanging = find_hanging_pieces(state["black_pieces"], "Black")
    white_hanging = find_hanging_pieces(state["white_pieces"], "White")
    undefended = find_undefended_high_value(state["white_pieces"], "White") + find_undefended_high_value(state["black_pieces"], "Black")
    special = board_special_notes(board, state)

    lines.append("")
    lines.append("CRITICAL WARNINGS:")
    lines.append(f"  Black hanging pieces: {', '.join(black_hanging) or 'none'}")
    lines.append(f"  White hanging pieces: {', '.join(white_hanging) or 'none'}")
    lines.append(f"  Undefended high-value pieces: {', '.join(undefended) or 'none'}")
    for note in special:
        lines.append(f"  {note}")

    # WHITE'S MOST DANGEROUS IDEAS — deterministic threats from the current position.
    # This helps the proposer identify what White wants to do, so it can pick
    # WHITE_THREAT accurately and avoid walking into known tactical shots.
    # Note: these are White's threats if it were White's turn (hypothetical).
    lines.append("")
    lines.append("WHITE'S MOST DANGEROUS IDEAS (if it were White's turn):")
    white_ideas = _compute_white_threats(board)
    if white_ideas:
        for idea in white_ideas:
            lines.append(f"  {idea}")
    else:
        lines.append("  No immediate tactical threats detected.")

    return "\n".join(lines)


def build_validation_summary(results: list[dict], label: str) -> str:
    lines = [label]
    for result in results:
        candidate = result.get("candidate")
        if not candidate:
            lines.append(f"- {result['label']}: {result['explanation']}")
            continue
        validation = result["validation"]
        verdict = "PASS" if validation["passed"] else "FAIL"
        lines.append(
            f"- {candidate['san']} / {candidate['move_token']}: {verdict} — {validation['explanation']}"
        )
        if validation["hard_failures"]:
            for failure in validation["hard_failures"]:
                lines.append(f"    hard: {failure}")
        elif validation["warnings"]:
            for warning in validation["warnings"][:2]:
                lines.append(f"    warn: {warning}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backend communication
# ---------------------------------------------------------------------------

def post_thought(url: str, game_id: str, ply: int, agent: str, content: str, phase: str):
    try:
        httpx.post(
            f"{url}/thought",
            json={
                "game_id": game_id,
                "ply": ply,
                "agent": agent,
                "content": content,
                "phase": phase,
            },
            headers={"X-Controller-Id": "runner"},
            timeout=5,
        )
    except Exception as exc:
        print(f"  [thought error] {exc}", file=sys.stderr)


def post_move(url: str, game_id: str, ply: int, move_uci: str) -> bool:
    try:
        resp = httpx.post(
            f"{url}/ai-move",
            json={
                "game_id": game_id,
                "ply": ply,
                "move_uci": move_uci,
            },
            headers={"X-Controller-Id": "runner"},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception as exc:
        print(f"  [move error] {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Validation loop
# ---------------------------------------------------------------------------

def validate_candidate(board: chess.Board, candidate: dict) -> dict:
    """Merge move-safety validation + claim verification (LINE + WHITE_THREAT)."""
    # Step 1: Move-safety validation via cmd_validate
    tried_tokens = []
    validation = None
    for token in [candidate["move_token"], candidate["san"]]:
        if token in tried_tokens:
            continue
        tried_tokens.append(token)
        validation = cmd_validate(board, token, as_json=True)
        if validation["legal"]:
            break

    if validation is None or not validation["legal"]:
        return {
            "input": candidate["move_token"],
            "legal": False,
            "passed": False,
            "uci": None,
            "san": candidate["san"],
            "hard_failures": ["Neither MOVE nor SAN could be parsed as a legal move."],
            "warnings": [],
            "explanation": f"{candidate['move_token']} / {candidate['san']} could not be parsed as a legal move.",
        }

    # Step 2: Verify claimed LINE
    line_str = candidate.get("line", "")
    line_tokens = line_str.split() if line_str else []
    if not line_tokens:
        validation["hard_failures"].append("CLAIM: Missing LINE.")
        validation["passed"] = False
    elif len(line_tokens) < 3:
        validation["hard_failures"].append(
            f"CLAIM: LINE must contain at least 3 plies, got {len(line_tokens)}."
        )
        validation["passed"] = False
    else:
        if len(line_tokens) > 3:
            validation["warnings"].append(
                f"CLAIM: LINE had {len(line_tokens)} plies; validated first 3 only."
            )
        line_result = verify_claimed_line(board, validation["uci"], line_tokens[:3])
        if line_result["hard_failures"]:
            for f in line_result["hard_failures"]:
                validation["hard_failures"].append(f"CLAIM: {f}")
            validation["passed"] = False
        for w in line_result.get("warnings", []):
            validation["warnings"].append(f"CLAIM: {w}")
        validation["line_verification"] = line_result

    # Step 3: Verify claimed WHITE_THREAT
    white_threat = candidate.get("white_threat", "")
    if not white_threat or white_threat.lower() in ("none", "-", "n/a", ""):
        validation["hard_failures"].append("CLAIM: Missing WHITE_THREAT.")
        validation["passed"] = False
    else:
        board_after = board.copy()
        board_after.push(chess.Move.from_uci(validation["uci"]))
        threat_result = verify_white_threat(board_after, white_threat)
        if threat_result["hard_failures"]:
            for f in threat_result["hard_failures"]:
                validation["hard_failures"].append(f"CLAIM: {f}")
            validation["passed"] = False
        for w in threat_result.get("warnings", []):
            validation["warnings"].append(f"CLAIM: {w}")

        # A real claimed threat must appear in the line, and the claimed follow-up
        # must bring Black back to a non-losing continuation.
        if (threat_result.get("is_real_threat") and line_tokens
                and not any("CLAIM" in hf for hf in validation["hard_failures"])):
            if len(line_tokens) >= 2:
                line_white_reply = line_tokens[1]
                threat_san = threat_result.get("threat_san", "")
                threat_material_loss = threat_result.get("material_loss", 0)
                severe_threat = (
                    "CHECKMATE" in threat_result.get("explanation", "")
                    or threat_material_loss >= 2
                )
                if line_white_reply != threat_san and line_white_reply != white_threat:
                    message = (
                        f"CLAIM: LINE assumes White plays '{line_white_reply}' but "
                        f"WHITE_THREAT '{threat_san}' is the claimed critical reply."
                    )
                    if severe_threat:
                        validation["hard_failures"].append(message)
                        validation["passed"] = False
                    else:
                        validation["warnings"].append(message)
                else:
                    line_result = validation.get("line_verification", {})
                    line_outcome = line_result.get("material_outcome")
                    if line_outcome is not None and line_outcome < 0:
                        message = (
                            f"CLAIM: LINE does not neutralize WHITE_THREAT cleanly "
                            f"(material delta: {line_outcome})."
                        )
                        if severe_threat or line_outcome <= -2:
                            validation["hard_failures"].append(message)
                            validation["passed"] = False
                        else:
                            validation["warnings"].append(message)
        validation["threat_verification"] = threat_result

    # Recompute explanation after claim checks
    if validation["hard_failures"]:
        validation["passed"] = False
        validation["explanation"] = validation["hard_failures"][0]

    return validation


def validate_batch(board: chess.Board, candidates: list[dict]) -> list[dict]:
    results = []
    for candidate in candidates:
        validation = validate_candidate(board, candidate)
        results.append({
            "candidate": candidate,
            "validation": validation,
            "label": candidate["san"],
            "explanation": validation["explanation"],
        })
    return results


def rank_passing_moves(results: list[dict]) -> list[dict]:
    passing = []
    for i, result in enumerate(results):
        if result["validation"]["passed"]:
            passing.append({**result, "proposer_rank": i})
    passing.sort(
        key=lambda result: (
            1 if len(result["validation"]["warnings"]) >= 3 else 0,
            result["proposer_rank"],
        )
    )
    return passing


def should_skip_critic(results: list[dict]) -> bool:
    """
    Skip the critic when the proposer's top-ranked candidate already passes cleanly.
    The critic is most useful as a tie-breaker among ambiguous survivors, not as a
    mandatory extra call when the first choice is already clean.
    """
    if not results:
        return False
    top = results[0]["validation"]
    return top["passed"] and not top["warnings"]


def build_retry_failure_text(results: list[dict], extra_reason: str | None = None) -> str:
    lines = []
    if extra_reason:
        lines.append(f"- {extra_reason}")
    for result in results:
        candidate = result.get("candidate")
        if not candidate:
            continue
        validation = result["validation"]
        # Separate claim failures from tactical/safety failures
        claim_failures = [f for f in validation.get("hard_failures", []) if f.startswith("CLAIM:")]
        tactical_failures = [f for f in validation.get("hard_failures", []) if not f.startswith("CLAIM:")]
        parts = []
        if tactical_failures:
            parts.append(f"TACTICAL: {' | '.join(tactical_failures)}")
        if claim_failures:
            parts.append(f"CLAIM: {' | '.join(f.removeprefix('CLAIM: ') for f in claim_failures)}")
        if not parts:
            parts.append(validation["explanation"])
        lines.append(f"- {candidate['san']}: REJECTED — {' ; '.join(parts)}")
    return "\n".join(lines)


def find_result_by_choice_token(board: chess.Board, passing: list[dict], token: str) -> dict | None:
    token = (token or "").strip()
    if not token:
        return None
    for result in passing:
        validation = result["validation"]
        candidate = result["candidate"]
        if token in {validation["san"], validation["uci"], candidate["san"], candidate["move_token"]}:
            return result

    move = parse_move_token(board, token)
    if move is None:
        return None
    for result in passing:
        if result["validation"]["uci"] == move.uci():
            return result
    return None


def apply_critic_choice(board: chess.Board, board_brief: str, provider, results: list[dict]) -> tuple[str | None, dict | None]:
    """Single LLM call to choose among survivors or propose one validated override."""
    passing = [result for result in results if result["validation"]["passed"]]
    considered = passing if passing else results
    if not considered:
        return None, None

    critic_mode = (
        "These candidate moves already passed a minimal tactical referee. "
        "Choose one survivor or override with one better BLACK move of your own."
        if passing else
        "All proposer candidates failed the minimal tactical referee. "
        "Review the rejected candidates and their failure reasons, then OVERRIDE with one better BLACK move of your own."
    )

    detail_lines = []
    for i, result in enumerate(considered):
        v = result["validation"]
        c = result["candidate"]
        line = c.get("line") or "No line provided."
        reasoning = c.get("reasoning", "none")
        if v["passed"]:
            warnings = ", ".join(v.get("warnings", [])[:2]) or "none"
            detail_lines.append(
                f"{i+1}. PASS {v['san']} ({v['uci']}) | LINE: {line} | REASONING: {reasoning} | WARNINGS: {warnings}"
            )
        else:
            failures = " | ".join(v.get("hard_failures", [])[:3]) or v.get("explanation", "rejected")
            detail_lines.append(
                f"{i+1}. FAIL {c.get('san', v.get('san', '?'))} ({c.get('move_token', v.get('uci', '?'))}) | "
                f"LINE: {line} | REASONING: {reasoning} | REJECTED: {failures}"
            )

    prompt = CRITIC_PROMPT.format(
        critic_mode=critic_mode,
        board_brief=board_brief,
        candidates_detail="\n".join(detail_lines),
    )
    raw = provider.call(prompt)
    choice = parse_critic_choice(raw)
    decision = choice.get("decision")
    move_token = choice.get("move")
    reasoning = choice.get("reasoning") or "No reasoning provided."
    line = choice.get("line") or "No line provided."

    if not decision or not move_token:
        return f"Critic output could not be parsed.\nRAW:\n{raw or 'No response.'}", None

    if decision == "CHOOSE":
        if not passing:
            return (
                f"Critic chose '{move_token}', but there were no surviving candidates to choose from.\n"
                f"RAW:\n{raw or 'No response.'}"
            ), None
        chosen = find_result_by_choice_token(board, passing, move_token)
        if not chosen:
            return (
                f"Critic chose '{move_token}', but it did not match any surviving candidate.\n"
                f"RAW:\n{raw or 'No response.'}"
            ), None
        summary = (
            f"Critic choice: CHOOSE {chosen['validation']['san']} ({chosen['validation']['uci']})\n"
            f"LINE: {line}\n"
            f"REASONING: {reasoning}"
        )
        return summary, {
            "source": "critic_choose",
            "move": chosen["validation"]["uci"],
            "san": chosen["validation"]["san"],
            "reason": reasoning,
        }

    override_validation = cmd_validate(board, move_token, as_json=True)
    if not override_validation["legal"]:
        return (
            f"Critic override '{move_token}' was illegal.\nRAW:\n{raw or 'No response.'}"
        ), None
    if not override_validation["passed"]:
        return (
            f"Critic override {override_validation['san']} ({override_validation['uci']}) failed validation: "
            f"{override_validation['explanation']}\nRAW:\n{raw or 'No response.'}"
        ), None

    summary = (
        f"Critic choice: OVERRIDE {override_validation['san']} ({override_validation['uci']})\n"
        f"LINE: {line}\n"
        f"REASONING: {reasoning}"
    )
    return summary, {
        "source": "critic_override",
        "move": override_validation["uci"],
        "san": override_validation["san"],
        "reason": reasoning,
    }


def deterministic_fallback(board: chess.Board) -> dict:
    ranked = []
    for move in board.legal_moves:
        validation = cmd_validate(board, move.uci(), as_json=True)
        board_after = board.copy()
        board_after.push(move)
        ranked.append({
            "uci": move.uci(),
            "san": board.san(move),
            "is_capture": board.is_capture(move),
            "is_check": board_after.is_check(),
            "validation": validation,
        })

    safe = [item for item in ranked if item["validation"]["passed"]]
    if safe:
        safe.sort(
            key=lambda item: (
                0 if item["is_check"] else 1,
                0 if item["is_capture"] else 1,
                len(item["validation"]["warnings"]),
                item["san"],
            )
        )
        choice = safe[0]
        return {
            "move": choice["uci"],
            "reason": f"Deterministic fallback chose {choice['san']} ({choice['uci']}) after validation.",
        }

    first = ranked[0]
    return {
        "move": first["uci"],
        "reason": f"Emergency fallback — every legal move failed validation, playing first legal move {first['san']} ({first['uci']}).",
    }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def play_move(url: str, game_state: dict, proposer_provider, critic_provider):
    fen = game_state["fen"]
    game_id = game_state["game_id"]
    ply = game_state["ply"]
    legal_moves = game_state["legal_moves"]
    board = chess.Board(fen)

    print(f"\n{'=' * 50}")
    print(f"Move {ply // 2 + 1}... (ply {ply}, {len(legal_moves)} legal moves)")

    if len(legal_moves) == 1:
        move = legal_moves[0]
        print(f"  Forced: {move}")
        post_thought(url, game_id, ply, "validation", f"Only legal move: {move}", "deciding")
        post_move(url, game_id, ply, move)
        return

    # --- Opening book branch ---
    book_result = get_book_move(board)
    if book_result:
        book_move = book_result.move
        opening = book_result.opening_name or "repertoire"
        print(f"  [BOOK] {book_move.san} ({opening}, {book_move.games} games, conf={book_result.confidence})", flush=True)
        post_thought(
            url, game_id, ply, "proposer",
            f"Opening book: {opening}\nPlaying {book_move.san} ({book_move.uci}) — "
            f"{book_move.games} master games, confidence {book_result.confidence}.\n"
            f"Proposer/validator loop skipped (book move).",
            "proposing",
        )
        post_thought(url, game_id, ply, "validation",
            f"Book move {book_move.san} — skipping validation (trusted repertoire).", "deciding")
        post_move(url, game_id, ply, book_move.uci)
        return

    board_brief = build_board_brief(board, game_state.get("move_history", []))
    proposer_prompt = PROPOSER_PROMPT.format(board_brief=board_brief)
    print("  [1] Proposer...", flush=True)
    proposer_raw = proposer_provider.call(proposer_prompt)
    proposer_candidates = parse_candidates(proposer_raw)
    print(f"      Got {len(proposer_candidates)} candidates: {[c['san'] for c in proposer_candidates]}", flush=True)

    post_thought(
        url,
        game_id,
        ply,
        "proposer",
        f"BOARD BRIEF:\n{board_brief}\n\nPROPOSER RESPONSE:\n{proposer_raw or 'No response.'}",
        "proposing",
    )

    if len(proposer_candidates) == 3:
        print("  [2] Validating...", flush=True)
        initial_results = validate_batch(board, proposer_candidates)
        for r in initial_results:
            v = r["validation"]
            status = "PASS" if v["passed"] else "FAIL"
            print(f"      {r['label']}: {status} — {v['explanation'][:80]}", flush=True)
        post_thought(url, game_id, ply, "validation", build_validation_summary(initial_results, "Initial validation"), "validating")
        if should_skip_critic(initial_results):
            print("  [3] Critic skipped — top proposer choice passed cleanly", flush=True)
            chosen = initial_results[0]
            move = chosen["validation"]["uci"]
            print(f"  => Playing {chosen['validation']['san']} ({move})", flush=True)
            reason = (
                f"Playing proposer top choice {chosen['validation']['san']} ({move}) — passed validation cleanly."
            )
            post_thought(url, game_id, ply, "validation", reason, "deciding")
            post_move(url, game_id, ply, move)
            return
        else:
            print("  [3] Critic...", flush=True)
            critic_summary, critic_choice = apply_critic_choice(board, board_brief, critic_provider, initial_results)
            if critic_summary:
                post_thought(url, game_id, ply, "validation", critic_summary, "validating")
                print(f"      Critic done", flush=True)
            if critic_choice:
                move = critic_choice["move"]
                san = critic_choice["san"]
                reason = (
                    f"Playing critic {critic_choice['source'].replace('_', ' ')} {san} ({move}). "
                    f"{critic_choice['reason']}"
                )
                post_thought(url, game_id, ply, "validation", reason, "deciding")
                post_move(url, game_id, ply, move)
                return
    else:
        parse_failure = f"Expected 3 unique candidates, got {len(proposer_candidates)}."
        post_thought(url, game_id, ply, "validation", build_validation_summary([{"label": "parse", "candidate": None, "explanation": parse_failure}], "Initial validation"), "validating")

    fallback = deterministic_fallback(board)
    post_thought(url, game_id, ply, "validation", fallback["reason"], "deciding")
    post_move(url, game_id, ply, fallback["move"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AgentChess Runner")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--provider", choices=["gemini", "claude"], default="gemini")
    parser.add_argument("--model", default=None, help="Model name override")
    parser.add_argument("--poll", type=float, default=2.0)
    args = parser.parse_args()

    if args.provider == "gemini":
        model = args.model or "gemini-2.5-flash"
        proposer_provider = GeminiProvider(model)
        print(f"Proposer: Gemini ({model})")
    else:
        model = args.model or "sonnet"
        proposer_provider = ClaudeProvider(model)
        print(f"Proposer: Claude CLI ({model})")

    critic_provider = GeminiProvider("gemini-3.1-pro-preview", thinking_budget=2048)
    print("Critic: Gemini (gemini-3.1-pro-preview, thinking_budget=2048)")

    print(f"Backend: {args.url}")
    print(f"Polling every {args.poll}s")
    print("Waiting for game...\n")

    consecutive_failures = 0

    while True:
        try:
            resp = httpx.get(f"{args.url}/game", timeout=5)
            if resp.status_code != 200:
                time.sleep(args.poll)
                continue

            state = resp.json()

            if state["status"] != "playing":
                winner = state.get("winner")
                print(f"\nGame over: {state['status']}" + (f". Winner: {winner}" if winner else ""))
                print("Waiting for new game...")
                old_id = state["game_id"]
                while True:
                    time.sleep(args.poll)
                    refreshed = httpx.get(f"{args.url}/game", timeout=5)
                    if refreshed.status_code == 200 and refreshed.json()["game_id"] != old_id:
                        print("New game!")
                        break
                continue

            if state["turn"] != "black":
                time.sleep(args.poll)
                continue

            try:
                play_move(args.url, state, proposer_provider, critic_provider)
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                print(f"  [error] {exc}", file=sys.stderr)
                import traceback
                traceback.print_exc()
                if consecutive_failures >= 3:
                    print("  Emergency fallback after 3 failures")
                    legal = state.get("legal_moves", [])
                    if legal:
                        post_move(args.url, state["game_id"], state["ply"], legal[0])
                    consecutive_failures = 0

        except httpx.ConnectError:
            pass
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as exc:
            print(f"[error] {exc}", file=sys.stderr)

        time.sleep(args.poll)


if __name__ == "__main__":
    main()
