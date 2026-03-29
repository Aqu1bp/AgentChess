# AgentChess Opening-Repertoire + Claim-Guided Validator Upgrade

## Summary
- Add a narrow opening-repertoire layer before the current proposer/validator loop so Black reaches stable middlegames without improvising from move 1.
- Upgrade the proposer contract from `MOVE | LINE | REASONING` to a single falsifiable claim contract: `MOVE | LINE | WHITE_THREAT | REASONING`.
- Verify the claimed line and claimed White threat deterministically before adding broader non-check tactical search.
- Add a small regression harness first, then expand it after book-era games; use it to gate all validator work.
- Keep the critic as an advisory tiebreaker only after deterministic checks pass.

## Key Changes

### 1. Baseline harness and opening repertoire
- Add a minimal regression corpus now in backend tests:
  - 10-20 positions total
  - mix of tactical refutations, known handoff positions, and one or two “safe move should still pass” cases
  - each case records `fen`, candidate move(s), and expected pass/fail or expected selected move
- Add a book adapter module in the backend that wraps the existing external explorer/repertoire code; the runner must not call the foreign folder directly.
- Add an opening-book branch at the top of the AI turn, before `build_board_brief()` / proposer flow in [runner.py](/Users/aquibmisbah/Desktop/agentchess/backend/runner.py#L618).
- Opening-book defaults:
  - vs `1.e4`: Caro-Kann family only
  - vs `1.d4`, `1.c4`, `1.Nf3`: QGD/Slav family only
  - explicitly exclude sharp families such as Najdorf, King’s Indian, Grunfeld, Dragon-type transpositions
- Book handoff defaults:
  - use book only through ply 12 total, or earlier if no allowed repertoire move exists
  - require a confident repertoire hit before playing a book move; otherwise hand off immediately to the proposer/validator loop
  - “confident” means the repertoire adapter returns a single preferred move or a clearly dominant top move within the filtered family
- Book branch thought behavior:
  - post one `proposer/proposing` thought summarizing repertoire family, top book options, and selected move
  - post one `validation/deciding` thought stating that the move came from the opening repertoire and the normal proposer/validator loop was skipped
- After 10 book-era games, expand the regression corpus with middlegame positions from those games and tag each case by motif/opening family.

### 2. Unified claim contract in the proposer
- Replace the proposer and retry format with:
  - `1. MOVE: <uci> (SAN: <san>) | LINE: <black_move> <white_reply> <black_followup> | WHITE_THREAT: <san-or-uci> | REASONING: <text>`
- `WHITE_THREAT` is a single concrete White move token, not free-form prose.
- `parse_candidates()` must require the new field for the primary parse path; keep the old `MOVE | LINE | REASONING` format only as a temporary compatibility fallback for one rollout cycle, then remove it.
- `build_board_brief()` must add an explicit `WHITE'S MOST DANGEROUS IDEAS` section derived from current deterministic facts:
  - legal White checks after Black candidate is not available yet, so this section should describe current-board threats only
  - include hanging-piece warnings, loose major pieces, obvious knight-jump forks, and currently available tactical captures/checks for White from the current position
- Retry prompts must include claim failures separately from general validation failures so the proposer learns whether it hallucinated the line, missed White’s threat, or simply chose a bad move.

### 3. Claim verification before broad search
- Keep `cmd_validate()` public CLI-compatible for move safety; do not change the existing CLI contract.
- Add internal deterministic claim-verification helpers in [perception.py](/Users/aquibmisbah/Desktop/agentchess/backend/perception.py):
  - `verify_claimed_line(board, candidate_move, line_tokens) -> dict`
  - `verify_white_threat(board_after_move, threat_token) -> dict`
- Claimed-line rules:
  - the first ply in `LINE` must normalize to the proposed move
  - all three plies must be legal in sequence
  - if the line is illegal or mismatched, mark the candidate as a hard failure
  - if the line is legal, compute its material/safety outcome and attach it to the validation summary
- White-threat rules:
  - `WHITE_THREAT` must normalize to a legal White reply from the post-move position
  - if illegal or unparseable, mark the candidate as a hard failure
  - if legal, simulate it and evaluate whether it creates a concrete tactical problem using the same baseline-vs-response logic as move validation
  - if it is a real strong threat, hard-fail the candidate unless Black’s claimed follow-up in `LINE` addresses it cleanly
  - if it is harmless, record it as a claim mismatch warning rather than a hard failure
- `validate_candidate()` in [runner.py](/Users/aquibmisbah/Desktop/agentchess/backend/runner.py#L441) must merge:
  - move-safety validation from `cmd_validate()`
  - claimed-line verification
  - White-threat verification
- Claim verification must run before critic logic and before broad non-check search.

### 4. Broad non-check tactical search as the safety net
- After move validation and claim verification, add a selective 2-ply non-check search over opponent replies from the post-move position.
- Do not brute-force all opponent moves equally. Search only replies matching one or more of these filters:
  - captures
  - checks
  - attacks on the moved piece
  - attacks on two or more Black pieces, including `king + rook` / `king + queen`
  - creation of a new hanging Black major or minor piece
  - newly opened lines toward the Black queen, rook, or king
  - trap patterns where the moved piece loses all safe squares after the reply
- For each selected reply, evaluate the net result after Black’s best immediate legal recapture/evasion and compare it against the pre-reply baseline; do not attribute unrelated pre-existing captures to the reply.
- Hard-fail criteria for the broad search:
  - forced net material loss of 2+ beyond baseline
  - king-plus-major-piece fork
  - trapped moved piece or trapped major/minor with no safe continuation
  - immediate mate or forced mate pattern already covered by current check logic
- Warnings only:
  - equal trades
  - additional pressure on a defended piece
  - harmless checks with no follow-up gain
- Keep the critic after this step, but only run it when 2+ candidates still pass. Critic output may demote survivors; it must not hard-reject a move on its own.

## Test Plan
- Regression harness:
  - seed 10-20 initial cases now
  - add a second dataset from 10 book-era games before tuning the non-check search thresholds
- Opening repertoire tests:
  - book hit returns a move from the allowed family
  - off-repertoire or low-confidence branches fall through to the proposer loop
  - handoff occurs at or before ply 12
  - book branch posts the expected thought events
- Proposer/claim parsing tests:
  - new `WHITE_THREAT` format parses correctly
  - temporary legacy format still parses during rollout
  - malformed `LINE` / malformed `WHITE_THREAT` fails cleanly
- Claim verification tests:
  - legal line that matches the candidate move passes
  - illegal claimed line hard-fails
  - illegal claimed White threat hard-fails
  - real claimed White threat causes rejection unless the claimed follow-up neutralizes it
  - harmless claimed White threat produces a warning, not a rejection
- Tactical-search tests:
  - non-check knight fork on two Black pieces is detected
  - discovered attack on the queen is detected
  - overloaded-defender / newly hanging major case is detected
  - back-rank mating pattern is detected
  - safe quiet move does not become a false positive
- Runner behavior:
  - book move bypasses proposer/validator loop
  - non-book move uses proposer → claim verification → broad search → critic → selection
  - critic only demotes among deterministic survivors
  - deterministic fallback still works if all candidates fail

## Assumptions and Defaults
- The external explorer/repertoire code already exists in another local folder and is accessible from a thin backend adapter.
- Initial repertoire is intentionally narrow and stability-biased:
  - Caro-Kann vs `1.e4`
  - QGD/Slav family vs `1.d4` / English / Réti move orders
- No frontend type changes are required; only thought content changes.
- Existing `perception.py validate` CLI remains stable; claim verification is added as internal backend logic rather than a new public CLI surface.
- Legacy proposer parsing remains for one rollout cycle only, then is removed once the runner is confirmed to emit the new contract consistently.
