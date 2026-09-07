"""Small, intentionally flawed fixture for testing an agent's coding workflow."""
def total_price(prices, discount=0):
    """Apply one fixed discount to the whole receipt; never return below zero."""
    return sum(price - discount for price in prices)
