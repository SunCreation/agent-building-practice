from pathlib import Path
import sys
import unittest

# 하네스는 -I(격리 모드)로 실행합니다. 테스트 대상 폴더만 명시적으로 추가합니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from budget import total_cost


class BudgetTests(unittest.TestCase):
    def test_venue_is_paid_once(self):
        self.assertEqual(total_cost(12, 5000, 100000), 160000)

    def test_empty_event_still_has_venue_cost(self):
        self.assertEqual(total_cost(0, 5000, 100000), 100000)

    def test_negative_input_rejected(self):
        for values in [(-1, 100, 10), (1, -100, 10), (1, 100, -10)]:
            with self.assertRaises(ValueError):
                total_cost(*values)
