"""
AgentChess Runner — Proposer + Validator controller.

The LLM proposes three ranked candidate moves from a grounded board brief.
A deterministic validator rejects tactical blunders, then surviving moves are
ranked with the proposer's order as the primary signal.
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
    best_evasion_balance_after_check,
    best_immediate_recapture_balance,
    cmd_state,
    cmd_validate,
    worst_capture_balance_after_response,
)
from opening_book import get_book_move

BACKEND_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class GeminiProvider:
    def __init__(self, model: str = "gemini-2.5-flash"):
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

    def call(self, prompt: str) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                self.genai.types.Content(
                    role="user",
                    parts=[self.genai.types.Part(text=prompt)],
                )
            ],
            config=self.genai.types.GenerateContentConfig(
                temperature=0.3,
            ),
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
- If you are unsure about the UCI, the SAN still needs to be correct.
- Return exactly 3 ranked moves in the exact format below.

Format EXACTLY:
CANDIDATES:
1. MOVE: <uci> (SAN: <san>) | LINE: <move1> <reply1> <move2> | REASONING: <why this is best>
2. MOVE: <uci> (SAN: <san>) | LINE: <move1> <reply1> <move2> | REASONING: <why this is next>
3. MOVE: <uci> (SAN: <san>) | LINE: <move1> <reply1> <move2> | REASONING: <why this is third>
"""


RETRY_PROMPT = """You are BLACK in a human-vs-AI chess game.

Your previous candidate moves were rejected by deterministic validation.

BOARD BRIEF:
{board_brief}

FAILED CANDIDATES:
{failures}

Pick 3 DIFFERENT moves. Do not repeat any failed move.

Rules:
- Use the rejection reasons as hard constraints.
- Keep using chess judgment: opening knowledge, tactical awareness, strategic plans.
- Return exactly 3 ranked moves in the exact format below.

Format EXACTLY:
CANDIDATES:
1. MOVE: <uci> (SAN: <san>) | LINE: <move1> <reply1> <move2> | REASONING: <why this avoids the failures>
2. MOVE: <uci> (SAN: <san>) | LINE: <move1> <reply1> <move2> | REASONING: <why this avoids the failures>
3. MOVE: <uci> (SAN: <san>) | LINE: <move1> <reply1> <move2> | REASONING: <why this avoids the failures>
"""


CRITIC_PROMPT = """You are WHITE. BLACK just played {move} in this position:

BOARD BRIEF:
{board_brief}

BLACK'S INTENDED LINE:
{line}

Find White's BEST response. Look for:
- Captures that win material
- Checks that fork pieces
- Moves that trap or attack multiple pieces

Return EXACTLY:
WHITE'S BEST REPLY: <san or uci>
REASONING: <what this achieves>
"""


# ---------------------------------------------------------------------------
# Parsing / formatting
# ---------------------------------------------------------------------------

def parse_candidates(text: str) -> list[dict]:
    candidates = []
    seen = set()
    line_pattern = re.compile(
        r"^\s*(\d+)\.\s*MOVE:\s*(\S+)\s*\(SAN:\s*([^)]+)\)\s*\|\s*LINE:\s*(.*?)\s*\|\s*REASONING:\s*(.+)$",
        re.MULTILINE,
    )
    fallback_pattern = re.compile(
        r"^\s*(\d+)\.\s*MOVE:\s*(\S+)\s*\(SAN:\s*([^)]+)\)\s*\|\s*REASONING:\s*(.+)$",
        re.MULTILINE,
    )

    def add_candidate(move_token: str, san: str, reasoning: str, line: str) -> None:
        key = (move_token, san)
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "move_token": move_token,
            "san": san,
            "line": line,
            "reasoning": reasoning,
        })

    for match in line_pattern.finditer(text):
        add_candidate(
            match.group(2).strip(),
            match.group(3).strip(),
            match.group(5).strip(),
            match.group(4).strip(),
        )
    for match in fallback_pattern.finditer(text):
        add_candidate(
            match.group(2).strip(),
            match.group(3).strip(),
            match.group(4).strip(),
            "",
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
    reply_match = re.search(r"WHITE'S BEST REPLY:\s*(.+)", text or "", re.MULTILINE)
    reasoning_match = re.search(r"REASONING:\s*(.+)", text or "", re.MULTILINE)
    reply = reply_match.group(1).strip() if reply_match else None
    reasoning = reasoning_match.group(1).strip() if reasoning_match else ""
    return reply, reasoning


def describe_piece_lines(title: str, pieces: list[dict]) -> list[str]:
    lines = [title]
    for piece in pieces:
        attacked_by = ", ".join(piece["attackers"]) or "none"
        defended_by = ", ".join(piece["defenders"]) or "none"
        shelter = ""
        if "pawn_shelter" in piece:
            shelter = f" | pawn shelter: {', '.join(piece['pawn_shelter'])}"
        lines.append(
            f"  {piece['piece'].capitalize()} {piece['square']} | attacked by: {attacked_by} | defended by: {defended_by}{shelter}"
        )
    return lines


def find_hanging_pieces(pieces: list[dict], color_name: str) -> list[str]:
    hanging = []
    for piece in pieces:
        if piece["piece"] != "king" and piece["attackers"] and not piece["defenders"]:
            hanging.append(f"{color_name} {piece['piece']} on {piece['square']}")
    return hanging


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
    tried_tokens = []
    for token in [candidate["move_token"], candidate["san"]]:
        if token in tried_tokens:
            continue
        tried_tokens.append(token)
        validation = cmd_validate(board, token, as_json=True)
        if validation["legal"]:
            return validation

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
            result.get("critic_penalty", 0),
            result["proposer_rank"],
        )
    )
    return passing


def build_retry_failure_text(results: list[dict], extra_reason: str | None = None) -> str:
    lines = []
    if extra_reason:
        lines.append(f"- {extra_reason}")
    for result in results:
        candidate = result.get("candidate")
        if not candidate:
            continue
        validation = result["validation"]
        explanation = validation["explanation"]
        if validation["hard_failures"]:
            explanation = " | ".join(validation["hard_failures"])
        lines.append(f"- {candidate['san']}: REJECTED — {explanation}")
    return "\n".join(lines)


def build_critic_summary(results: list[dict], label: str) -> str:
    lines = [label]
    for result in results:
        if not result["validation"]["passed"]:
            continue
        note = result.get("critic_note")
        if not note:
            continue
        lines.append(f"- {result['validation']['san']}: {note}")
    return "\n".join(lines)


def apply_critic_feedback(board: chess.Board, board_brief: str, provider, results: list[dict]) -> str | None:
    passing = [result for result in results if result["validation"]["passed"]]
    if len(passing) < 2:
        return None

    mover_color = board.turn
    any_note = False
    for result in passing:
        validation = result["validation"]
        candidate = result["candidate"]
        board_after = board.copy()
        board_after.push(chess.Move.from_uci(validation["uci"]))

        baseline = worst_capture_balance_after_response(board_after, mover_color)
        prompt = CRITIC_PROMPT.format(
            move=f"{validation['san']} ({validation['uci']})",
            board_brief=board_brief,
            line=candidate.get("line") or "No line provided.",
        )
        raw = provider.call(prompt)
        reply_token, reasoning = parse_critic_reply(raw)

        note = "Critic response could not be parsed."
        penalty = 0
        if reply_token:
            reply_move = parse_move_token(board_after, reply_token)
            if reply_move is None:
                note = f"Critic suggested {reply_token}, which could not be parsed."
            else:
                reply_san = board_after.san(reply_move)
                reply_board = board_after.copy()
                reply_board.push(reply_move)
                if reply_board.is_checkmate():
                    penalty = 1
                    note = f"Critic found {reply_san}, which is immediate mate."
                else:
                    if reply_board.is_check():
                        post_reply_balance = best_evasion_balance_after_check(reply_board, mover_color)
                    else:
                        post_reply_balance = best_immediate_recapture_balance(reply_board, mover_color)
                    extra_loss = baseline - post_reply_balance
                    if extra_loss >= 2:
                        penalty = 1
                        note = f"Critic found {reply_san}, worsening material by {extra_loss} beyond baseline."
                    else:
                        note = f"Critic suggested {reply_san}, but it does not worsen material beyond baseline."
                if reasoning:
                    note = f"{note} {reasoning}"

        result["critic_penalty"] = penalty
        result["critic_note"] = note
        any_note = True

    if not any_note:
        return None
    return build_critic_summary(results, "Critic review")


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

def play_move(url: str, game_state: dict, provider):
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
    proposer_raw = provider.call(proposer_prompt)
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

    all_failed_tokens = set()
    for candidate in proposer_candidates:
        all_failed_tokens.add(candidate["move_token"])
        all_failed_tokens.add(candidate["san"])

    initial_results = []
    retry_failures = None
    if len(proposer_candidates) == 3:
        print("  [2] Validating...", flush=True)
        initial_results = validate_batch(board, proposer_candidates)
        for r in initial_results:
            v = r["validation"]
            status = "PASS" if v["passed"] else "FAIL"
            print(f"      {r['label']}: {status} — {v['explanation'][:80]}", flush=True)
        post_thought(url, game_id, ply, "validation", build_validation_summary(initial_results, "Initial validation"), "validating")
        print("  [3] Critic...", flush=True)
        critic_summary = apply_critic_feedback(board, board_brief, provider, initial_results)
        if critic_summary:
            post_thought(url, game_id, ply, "validation", critic_summary, "validating")
            print(f"      Critic done", flush=True)
        ranked = rank_passing_moves(initial_results)
        if ranked:
            chosen = ranked[0]
            move = chosen["validation"]["uci"]
            print(f"  => Playing {chosen['validation']['san']} ({move})", flush=True)
            reason = (
                f"Playing proposer choice {chosen['validation']['san']} ({move}) — top surviving candidate after validation."
            )
            post_thought(url, game_id, ply, "validation", reason, "deciding")
            post_move(url, game_id, ply, move)
            return
        print("  [!] All failed, retrying...", flush=True)
        retry_failures = build_retry_failure_text(initial_results)
    else:
        parse_failure = f"Expected 3 unique candidates, got {len(proposer_candidates)}."
        post_thought(url, game_id, ply, "validation", build_validation_summary([{"label": "parse", "candidate": None, "explanation": parse_failure}], "Initial validation"), "validating")
        retry_failures = parse_failure

    print("  [4] Retry proposer...", flush=True)
    retry_prompt = RETRY_PROMPT.format(board_brief=board_brief, failures=retry_failures)
    retry_raw = provider.call(retry_prompt)
    retry_candidates = [
        candidate for candidate in parse_candidates(retry_raw)
        if candidate["move_token"] not in all_failed_tokens and candidate["san"] not in all_failed_tokens
    ]

    post_thought(
        url,
        game_id,
        ply,
        "proposer",
        f"BOARD BRIEF:\n{board_brief}\n\nRETRY FAILURES:\n{retry_failures}\n\nRETRY RESPONSE:\n{retry_raw or 'No response.'}",
        "proposing",
    )

    if len(retry_candidates) == 3:
        retry_results = validate_batch(board, retry_candidates)
        post_thought(url, game_id, ply, "validation", build_validation_summary(retry_results, "Retry validation"), "validating")
        critic_summary = apply_critic_feedback(board, board_brief, provider, retry_results)
        if critic_summary:
            post_thought(url, game_id, ply, "validation", critic_summary, "validating")
        ranked = rank_passing_moves(retry_results)
        if ranked:
            chosen = ranked[0]
            move = chosen["validation"]["uci"]
            reason = (
                f"Playing retry candidate {chosen['validation']['san']} ({move}) — top surviving retry move after validation."
            )
            post_thought(url, game_id, ply, "validation", reason, "deciding")
            post_move(url, game_id, ply, move)
            return
    else:
        parse_failure = f"Retry expected 3 new unique candidates, got {len(retry_candidates)}."
        post_thought(
            url,
            game_id,
            ply,
            "validation",
            build_validation_summary([{"label": "retry-parse", "candidate": None, "explanation": parse_failure}], "Retry validation"),
            "validating",
        )

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
        provider = GeminiProvider(model)
        print(f"Provider: Gemini ({model})")
    else:
        model = args.model or "sonnet"
        provider = ClaudeProvider(model)
        print(f"Provider: Claude CLI ({model})")

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
                play_move(args.url, state, provider)
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
