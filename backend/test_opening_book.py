"""
Tests for the opening book adapter.

Run: python3 -m pytest test_opening_book.py -v
"""

import unittest
from unittest.mock import patch

import chess
import opening_book
from opening_book import get_book_move, MAX_PLY


class OpeningBookTests(unittest.TestCase):
    def tearDown(self):
        opening_book._cache.clear()

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
        b = chess.Board()
        for m in ["e4", "e5", "Nf3"]:
            b.push_san(m)
        with patch.object(opening_book, "_fetch_explorer", return_value={
            "opening": {"name": "King's Pawn Game"},
            "moves": [
                {"uci": "b8c6", "san": "Nc6", "white": 30, "draws": 20, "black": 50},
            ],
        }):
            result = get_book_move(b)
        self.assertIsNone(result)

    def test_book_move_is_legal(self):
        """Book move must always be legal in the position."""
        positions = [
            ["e4"],           # 1.e4
            ["d4"],           # 1.d4
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

    def test_book_only_selects_moves_that_stay_in_repertoire(self):
        """Allowed positions must still reject continuations that leave the repertoire."""
        b = chess.Board()
        for m in ["d4", "d5", "c4"]:
            b.push_san(m)

        current_fen = b.fen()
        c5_board = b.copy()
        c5_board.push_san("c5")
        e6_board = b.copy()
        e6_board.push_san("e6")

        def fake_fetch(fen: str):
            if fen == current_fen:
                return {
                    "opening": {"name": "Queen's Gambit"},
                    "moves": [
                        {"uci": "c7c5", "san": "c5", "white": 20, "draws": 10, "black": 70},
                        {"uci": "e7e6", "san": "e6", "white": 30, "draws": 20, "black": 40},
                    ],
                }
            if fen == c5_board.fen():
                return {
                    "opening": {"name": "Benoni Defense"},
                    "moves": [],
                }
            if fen == e6_board.fen():
                return {
                    "opening": {"name": "Queen's Gambit Declined"},
                    "moves": [],
                }
            return None

        with patch.object(opening_book, "_fetch_explorer", side_effect=fake_fetch):
            result = get_book_move(b)

        self.assertIsNotNone(result)
        self.assertEqual("e6", result.move.san)


if __name__ == "__main__":
    unittest.main()
