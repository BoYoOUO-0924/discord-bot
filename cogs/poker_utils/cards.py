import secrets
from typing import List

class Card:
    SUITS = ["♠", "♥", "♦", "♣"]
    RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
    RANK_VALUES = {rank: idx for idx, rank in enumerate(RANKS)}

    def __init__(self, rank: str, suit: str) -> None:
        self.rank = rank
        self.suit = suit

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"

    def value(self) -> int:
        return Card.RANK_VALUES[self.rank]

def generate_shuffled_deck() -> List[Card]:
    deck = [Card(rank, suit) for suit in Card.SUITS for rank in Card.RANKS]
    for i in range(len(deck) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        deck[i], deck[j] = deck[j], deck[i]
    return deck
