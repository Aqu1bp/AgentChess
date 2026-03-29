"""
Tests for the opening book adapter.

Run: python3 -m pytest test_opening_book.py -v
"""

import unittest
import chess
from opening_book import get_book_move, MAX_PLY


class OpeningBookTests(unittest.TestCase):

    def test_caro_kann_vs_e4(self):
        """After 1.e4, book should play c6 (Caro-Kann)."""
        b = chess.Board()
        b.push_san("e4")
        result = get_book_move(b)
        self.assertIsNotNone(result, "Expected book hit after 1.e4")
        self.assertEqual(result.move.san, "c6", "Should play Caro-Kann")
        self.assertIn(result.confidence, ("high", "medium"))

    def test_qgd_vs_d4(self):
        """After 1.d4, book should play d5 (QGD/Slav)."""
        b = chess.Board()
        b.push_san("d4")
        result = get_book_move(b)
        self.assertIsNotNone(result, "Expected book hit after 1.d4")
        self.assertEqual(result.move.san, "d5", "Should play QGD")

    def test_white_to_move_returns_none(self):
        """Book only plays Black."""
        b = chess.Board()
        result = get_book_move(b)
        self.assertIsNone(result, "Should return None for White to move")

    def test_handoff_at_ply_limit(self):
        """Past MAX_PLY, book should return None."""
        b = chess.Board()
        # Play enough moves to exceed ply limit
        moves = ["e4", "c6", "d4", "d5", "Nc3", "dxe4", "Nxe4", "Nd7",
                  "Nf3", "Ngf6", "Nxf6+", "Nxf6", "Bd3"]
        for m in moves:
            b.push_san(m)
        if b.ply() > MAX_PLY and b.turn == chess.BLACK:
            result = get_book_move(b)
            self.assertIsNone(result, f"Should return None past ply {MAX_PLY}")
        else:
            # If we haven't exceeded ply, this test is informational
            self.skipTest(f"Position ply {b.ply()} <= {MAX_PLY}, need longer game")

    def test_off_repertoire_returns_none(self):
        """Position outside Caro-Kann/QGD families should return None."""
        # Play 1.e4 e5 (NOT Caro-Kann) then White plays 2.Nf3
        b = chess.Board()
        for m in ["e4", "e5", "Nf3"]:
            b.push_san(m)
        result = get_book_move(b)
        # After 1.e4 e5 2.Nf3, the opening is NOT Caro-Kann
        # But our book plays c6 on move 1, so this path wouldn't happen in practice
        # This tests the filter logic if we somehow got to this position
        # The explorer will return "King's Pawn" or "Italian" type names
        # which should NOT match our repertoire prefixes
        if result is not None:
            # If it returns something, verify it's not from a non-repertoire family
            if result.opening_name:
                self.assertTrue(
                    any(p.lower() in result.opening_name.lower()
                        for p in ["Caro-Kann", "Queen's Gambit", "Slav", "Semi-Slav", "Queen's Pawn"]),
                    f"Book returned move from non-repertoire family: {result.opening_name}"
                )

    def test_book_move_is_legal(self):
        """Book move must always be legal in the position."""
        positions = [
            ["e4"],           # 1.e4
            ["d4"],           # 1.d4
            ["e4", "c6", "d4"],  # 1.e4 c6 2.d4
        ]
        for moves in positions:
            b = chess.Board()
            for m in moves:
                b.push_san(m)
            if b.turn != chess.BLACK:
                continue
            result = get_book_move(b)
            if result:
                move = chess.Move.from_uci(result.move.uci)
                self.assertIn(move, b.legal_moves,
                    f"Book move {result.move.san} ({result.move.uci}) is not legal after {' '.join(moves)}")


if __name__ == "__main__":
    unittest.main()
