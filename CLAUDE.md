# AgentChess

Proposer + validator chess system where Claude Code plays black against a human.

## Tech Stack
- **Frontend:** React 18 + TypeScript + Vite + Tailwind, board engine from EvalMax
- **Backend:** Python FastAPI on localhost:8000, python-chess for game state + perception
- **AI:** Claude Code proposes moves from a grounded board brief, then a deterministic validator rejects blunders

## Running

```bash
# Terminal 1: Backend
cd backend && source venv/bin/activate && python3 -m uvicorn main:app --reload --port 8000

# Terminal 2: Frontend
cd frontend && npm run dev
```

Browser: http://localhost:5173

## How I Play Chess

I am the AI. I play black. When it's my turn, I follow this decision loop:

### Step 0: Check game state
```bash
curl -s http://localhost:8000/game | python3 -m json.tool
```
Confirm it is black's turn, status is `playing`, and note `game_id` plus `ply`.

### Step 1: Build the board brief
```bash
cd /Users/aquibmisbah/Desktop/agentchess/backend && source venv/bin/activate
python3 perception.py state --fen "<FEN>"
```
This is the grounded board brief: full piece placement, attackers/defenders, material, pawn structure, and legal tactical moves.

### Step 2: Propose 3 ranked moves
The proposer sees the board brief and returns exactly 3 ranked candidates:

```text
CANDIDATES:
1. MOVE: <uci> (SAN: <san>) | REASONING: <why this is best>
2. MOVE: <uci> (SAN: <san>) | REASONING: <why this is next>
3. MOVE: <uci> (SAN: <san>) | REASONING: <why this is third>
```

Post the proposer thought:

```bash
curl -s -X POST http://localhost:8000/thought \
  -H "Content-Type: application/json" \
  -d '{"game_id":"<ID>","ply":<PLY>,"agent":"proposer","content":"<board brief + proposer response>","phase":"proposing"}'
```

### Step 3: Validate in rank order
For each candidate, run deterministic validation:

```bash
python3 perception.py validate --fen "<FEN>" --move "<SAN_OR_UCI>"
```

Validation checks:
- legality
- immediate mate allowed for White
- free material losses with no immediate equalizing recapture
- quiet hostile replies that trap the moved piece
- new hanging black pieces

Post validation results:

```bash
curl -s -X POST http://localhost:8000/thought \
  -H "Content-Type: application/json" \
  -d '{"game_id":"<ID>","ply":<PLY>,"agent":"validation","content":"<validation report>","phase":"validating"}'
```

### Step 4: Retry once if all 3 fail
If all three proposed moves fail validation, send the proposer the exact rejection reasons and ask for 3 different moves.

Post the retry proposer response with `agent="proposer"` and `phase="proposing"`.

### Step 5: Decide
- If a candidate passes, play the **highest-ranked passing move**.
- If the retry batch also fails, choose a deterministic fallback:
  - first validated safe move
  - prefer checks, then captures, then fewer warnings
  - if no move passes, play the first legal move

Post the final decision:

```bash
curl -s -X POST http://localhost:8000/thought \
  -H "Content-Type: application/json" \
  -d '{"game_id":"<ID>","ply":<PLY>,"agent":"validation","content":"<decision reasoning>","phase":"deciding"}'
```

### Step 6: Play the move
```bash
curl -s -X POST http://localhost:8000/ai-move \
  -H "Content-Type: application/json" \
  -d '{"game_id":"<ID>","ply":<PLY>,"move_uci":"<UCI_MOVE>"}'
```

## Proposer Prompt Contract

```text
You are BLACK in a human-vs-AI chess game.

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
1. MOVE: <uci> (SAN: <san>) | REASONING: <why this is best>
2. MOVE: <uci> (SAN: <san>) | REASONING: <why this is next>
3. MOVE: <uci> (SAN: <san>) | REASONING: <why this is third>
```

## Error Recovery
- **Parse failure** from proposer: retry once and require 3 correctly formatted moves.
- **All candidates fail validation:** retry once with exact grounded rejection reasons.
- **Retry fails too:** deterministic fallback, then post an emergency note.
- **API error:** retry with 2s backoff, max 2 retries.
- **3 consecutive runner failures:** play the first legal move and recover on the next ply.

## Perception CLI Reference

```bash
# Full board state
python3 perception.py state --fen "..."

# All legal moves
python3 perception.py legal --fen "..."

# Simulate a line
python3 perception.py simulate --fen "..." --move Nf3 --move Nc6

# Query a square
python3 perception.py query --fen "..." --square e4

# Validate one move
python3 perception.py validate --fen "..." --move "Nxd5"

# JSON output
python3 perception.py state --fen "..." --json
python3 perception.py validate --fen "..." --move "e7e5" --json
```
