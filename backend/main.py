"""
AgentChess Backend — FastAPI

Lightweight game state server. The AI brain lives elsewhere (Claude Code or runner.py).
This server holds the authoritative board, validates moves, and streams agent thoughts via SSE.
"""

import asyncio
import json
import threading
import time
import uuid
from collections import deque
from typing import Optional

import chess
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="AgentChess", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class MoveRequest(BaseModel):
    from_sq: str
    to_sq: str
    promotion: Optional[str] = None


class AIMoveRequest(BaseModel):
    game_id: str
    ply: int
    move_uci: str


class ThoughtRequest(BaseModel):
    game_id: str
    ply: int
    agent: str       # "proposer" | "validation"
    content: str     # Complete atomic message — frontend appends, never replaces
    phase: str       # "proposing" | "validating" | "deciding"


class GameStateResponse(BaseModel):
    game_id: str
    fen: str
    ply: int
    turn: str              # "white" | "black"
    legal_moves: list[str] # UCI format
    last_move: Optional[dict]
    move_history: list[str] # SAN list
    status: str            # "playing" | "checkmate" | "stalemate" | "draw_fifty" | "draw_repetition" | "draw_insufficient" | "resigned"
    winner: Optional[str]


# ---------------------------------------------------------------------------
# Game State
# ---------------------------------------------------------------------------

class GameManager:
    def __init__(self):
        # SSE subscribers persist across game resets
        self.subscribers: list[asyncio.Queue] = []
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.game_id = str(uuid.uuid4())
            self.board = chess.Board()
            self.move_history_san: list[str] = []
            self.last_move: Optional[dict] = None
            # SSE — keep subscribers, clear events
            self.event_counter = 0
            self.event_buffer: deque = deque(maxlen=200)
            # Thinking lock
            self.thinking_controller: Optional[str] = None
            self.thinking_ply: Optional[int] = None

    @property
    def ply(self) -> int:
        return len(self.move_history_san)

    @property
    def turn(self) -> str:
        return "white" if self.board.turn == chess.WHITE else "black"

    @property
    def status(self) -> str:
        if self.board.is_checkmate():
            return "checkmate"
        if self.board.is_stalemate():
            return "stalemate"
        if self.board.is_insufficient_material():
            return "draw_insufficient"
        if self.board.can_claim_fifty_moves():
            return "draw_fifty"
        if self.board.can_claim_threefold_repetition():
            return "draw_repetition"
        return "playing"

    @property
    def winner(self) -> Optional[str]:
        if self.board.is_checkmate():
            # The side to move is in checkmate, so the other side won
            return "black" if self.board.turn == chess.WHITE else "white"
        return None

    def legal_moves_uci(self) -> list[str]:
        return [m.uci() for m in self.board.legal_moves]

    def to_response(self) -> GameStateResponse:
        return GameStateResponse(
            game_id=self.game_id,
            fen=self.board.fen(),
            ply=self.ply,
            turn=self.turn,
            legal_moves=self.legal_moves_uci(),
            last_move=self.last_move,
            move_history=self.move_history_san,
            status=self.status,
            winner=self.winner,
        )

    def push_move(self, move: chess.Move) -> str:
        """Push a move, record SAN, return SAN."""
        san = self.board.san(move)
        self.last_move = {
            "from": chess.square_name(move.from_square),
            "to": chess.square_name(move.to_square),
            "san": san,
            "uci": move.uci(),
        }
        self.board.push(move)
        self.move_history_san.append(san)
        # Clear thinking lock for new ply
        self.thinking_controller = None
        self.thinking_ply = None
        return san

    def try_claim_thinking(self, controller_id: str, ply: int) -> bool:
        """Try to claim the thinking lock for this ply. Returns True if granted."""
        if self.thinking_ply != ply:
            # New ply — grant to first claimer
            self.thinking_ply = ply
            self.thinking_controller = controller_id
            return True
        return self.thinking_controller == controller_id

    # SSE event management
    def broadcast_event(self, event_type: str, data: dict):
        """Send an SSE event to all subscribers and buffer it."""
        self.event_counter += 1
        event = {
            "id": self.event_counter,
            "type": event_type,
            "data": data,
            "timestamp": time.time(),
        }
        self.event_buffer.append(event)
        for queue in self.subscribers:
            queue.put_nowait(event)

    def get_replay(self, after_id: int) -> list[dict]:
        """Get buffered events after the given event ID."""
        return [e for e in self.event_buffer if e["id"] > after_id]


game = GameManager()

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/game")
def get_game() -> GameStateResponse:
    return game.to_response()


@app.post("/move")
def human_move(req: MoveRequest) -> GameStateResponse:
    """Human plays a move. Only allowed when it's white's turn (human = white)."""
    with game.lock:
        if game.status != "playing":
            raise HTTPException(409, f"Game is over: {game.status}")
        if game.turn != "white":
            raise HTTPException(409, "Not your turn — waiting for AI")

        # Parse move
        from_sq = chess.parse_square(req.from_sq)
        to_sq = chess.parse_square(req.to_sq)
        promotion = None
        if req.promotion:
            promo_map = {"q": chess.QUEEN, "r": chess.ROOK, "b": chess.BISHOP, "n": chess.KNIGHT}
            promotion = promo_map.get(req.promotion.lower())

        move = chess.Move(from_sq, to_sq, promotion=promotion)
        if move not in game.board.legal_moves:
            raise HTTPException(400, f"Illegal move: {req.from_sq}{req.to_sq}")

        san = game.push_move(move)
        game.broadcast_event("move", {
            "side": "white",
            "san": san,
            "uci": move.uci(),
            "from": req.from_sq,
            "to": req.to_sq,
            "fen": game.board.fen(),
            "ply": game.ply,
            "status": game.status,
        })

        return game.to_response()


@app.post("/ai-move")
def ai_move(req: AIMoveRequest, request: Request) -> GameStateResponse:
    """AI controller pushes a move. Requires matching game_id and ply."""
    with game.lock:
        if req.game_id != game.game_id:
            raise HTTPException(409, f"Stale game_id: expected {game.game_id}")
        if req.ply != game.ply:
            raise HTTPException(409, f"Stale ply: expected {game.ply}, got {req.ply}")
        if game.status != "playing":
            raise HTTPException(409, f"Game is over: {game.status}")
        if game.turn != "black":
            raise HTTPException(409, "Not AI's turn")

        # Verify controller lock
        controller_id = request.headers.get("X-Controller-Id", "manual")
        if game.thinking_controller and game.thinking_controller != controller_id:
            raise HTTPException(409, f"Another controller ({game.thinking_controller}) is thinking for this ply")

        # Parse UCI move
        try:
            move = chess.Move.from_uci(req.move_uci)
        except ValueError:
            raise HTTPException(400, f"Invalid UCI move: {req.move_uci}")
        if move not in game.board.legal_moves:
            raise HTTPException(400, f"Illegal move: {req.move_uci}")

        san = game.push_move(move)
        game.broadcast_event("move", {
            "side": "black",
            "san": san,
            "uci": req.move_uci,
            "from": chess.square_name(move.from_square),
            "to": chess.square_name(move.to_square),
            "fen": game.board.fen(),
            "ply": game.ply,
            "status": game.status,
        })

        return game.to_response()


@app.post("/thought")
def post_thought(req: ThoughtRequest, request: Request):
    """AI controller posts a thought. Requires matching game_id, ply, and controller lock."""
    with game.lock:
        if req.game_id != game.game_id:
            raise HTTPException(409, f"Stale game_id: expected {game.game_id}")
        if req.ply != game.ply:
            raise HTTPException(409, f"Stale ply: expected {game.ply}, got {req.ply}")

        controller_id = request.headers.get("X-Controller-Id", "manual")
        if not game.try_claim_thinking(controller_id, req.ply):
            raise HTTPException(409, f"Controller {game.thinking_controller} already claimed this ply")

        game.broadcast_event("thought", {
            "agent": req.agent,
            "content": req.content,
            "phase": req.phase,
            "ply": req.ply,
        })
        return {"ok": True}


@app.post("/new-game")
def new_game() -> GameStateResponse:
    """Reset to a new game. Keeps SSE subscribers alive."""
    game.reset()
    game.broadcast_event("game_state", {
        "action": "new_game",
        "game_id": game.game_id,
        "fen": game.board.fen(),
    })
    return game.to_response()


@app.get("/stream")
async def stream(request: Request, last_event_id: Optional[int] = None):
    """SSE endpoint. Streams thoughts, moves, and game state changes."""
    queue: asyncio.Queue = asyncio.Queue()
    game.subscribers.append(queue)

    async def event_generator():
        try:
            # Replay missed events if reconnecting
            replay_from = last_event_id or 0
            if replay_from > 0:
                for event in game.get_replay(replay_from):
                    yield format_sse(event)

            # Stream new events
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield format_sse(event)
                except asyncio.TimeoutError:
                    # Send keepalive ping
                    yield f"event: ping\ndata: {{}}\n\n"
        finally:
            game.subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def format_sse(event: dict) -> str:
    """Format a dict as an SSE message."""
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event['data'])}\n\n"
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "game_id": game.game_id}
