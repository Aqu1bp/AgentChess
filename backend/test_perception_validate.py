import unittest

import chess

from perception import cmd_validate


class ValidateMoveTests(unittest.TestCase):
    def test_illegal_move_is_rejected(self):
        board = chess.Board()
        result = cmd_validate(board, "e7e5", as_json=True)
        self.assertFalse(result["legal"])
        self.assertFalse(result["passed"])
        self.assertIn("Illegal", result["hard_failures"][0])

    def test_en_passant_is_parsed_and_normalized(self):
        board = chess.Board("rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3")
        result = cmd_validate(board, "exd6", as_json=True)
        self.assertTrue(result["legal"])
        self.assertEqual(result["san"], "exd6")
        self.assertEqual(result["uci"], "e5d6")

    def test_promotion_is_parsed_and_normalized(self):
        board = chess.Board("7k/P7/8/8/8/8/8/K7 w - - 0 1")
        result = cmd_validate(board, "a8=Q+", as_json=True)
        self.assertTrue(result["legal"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["uci"], "a7a8q")
        self.assertEqual(result["san"], "a8=Q+")

    def test_castling_is_supported(self):
        # Use a more realistic position where castling is safe
        # Standard Italian: 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nf6 4.d3 Be7 — Black can O-O safely
        board = chess.Board("r1bqk2r/ppppbppp/2n2n2/4p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R b KQkq - 0 4")
        result = cmd_validate(board, "O-O", as_json=True)
        self.assertTrue(result["legal"])
        self.assertTrue(result["passed"], f"O-O should pass in this position: {result['hard_failures']}")
        self.assertEqual(result["uci"], "e8g8")

    def test_quiet_trap_reply_is_detected(self):
        board = chess.Board("4k3/8/2n5/3B4/8/3P4/PP6/6K1 b - - 0 1")
        result = cmd_validate(board, "c6a5", as_json=True)
        self.assertFalse(result["passed"])
        self.assertIn("b4 traps the moved piece on a5", result["hard_failures"][0])
        self.assertEqual(result["quiet_hostile_replies"][0]["san"], "b4")

    def test_immediate_mate_reply_is_detected(self):
        board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p2Q/2B5/8/PPPP1PPP/RNB1K1NR b KQkq - 2 3")
        result = cmd_validate(board, "a6", as_json=True)
        self.assertFalse(result["passed"])
        self.assertIn("Qxf7# is immediate checkmate for White.", result["hard_failures"])

    def test_free_piece_loss_is_detected(self):
        board = chess.Board("4k3/8/2n5/8/8/P7/8/6K1 b - - 0 1")
        result = cmd_validate(board, "c6b4", as_json=True)
        self.assertFalse(result["passed"])
        self.assertIn("axb4 wins black knight with no immediate equalizing recapture.", result["hard_failures"])

    def test_check_fork_with_losing_evasions_is_detected(self):
        board = chess.Board()
        for san in ["d4", "d5", "Nc3", "Nf6", "Bf4", "Nc6", "Nb5"]:
            board.push_san(san)

        result = cmd_validate(board, "a6", as_json=True)

        self.assertFalse(result["passed"])
        self.assertIn("Nxc7+", [item["san"] for item in result["opponent_checks"]])
        self.assertTrue(
            any("every evasion loses material beyond baseline" in item for item in result["hard_failures"])
        )

    def test_harmless_check_stays_a_warning(self):
        board = chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")

        result = cmd_validate(board, "d5", as_json=True)

        self.assertTrue(result["passed"])
        self.assertIn("Bb5+", [item["san"] for item in result["opponent_checks"]])
        self.assertFalse(any("every evasion loses material beyond baseline" in item for item in result["hard_failures"]))


if __name__ == "__main__":
    unittest.main()
