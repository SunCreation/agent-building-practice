import unittest
from receipt import total_price

class ReceiptTests(unittest.TestCase):
    def test_discount_once(self):
        self.assertEqual(total_price([12, 8], 3), 17)
    def test_empty(self):
        self.assertEqual(total_price([], 3), 0)
    def test_clamp_at_zero(self):
        self.assertEqual(total_price([2], 5), 0)

if __name__ == "__main__":
    unittest.main()
