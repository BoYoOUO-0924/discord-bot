from collections import Counter
import itertools
from typing import List, Tuple

from .cards import Card

# Hand rank names mapping
HAND_RANK_NAMES = {
    8: "同花順",
    7: "四條",
    6: "葫蘆",
    5: "同花",
    4: "順子",
    3: "三條",
    2: "兩對",
    1: "一對",
    0: "高牌"
}

def get_hand_name(rank: int, kicker_values: List[int]) -> str:
    """Gets the Chinese name of the hand rank."""
    if rank == 8 and kicker_values[0] == 12:
        return "皇家同花順"
    return HAND_RANK_NAMES.get(rank, "未知牌型")


def evaluate_hand(hole: List[Card], community: List[Card]) -> Tuple[int, List[int], str, List[Card]]:
    """
    Evaluates the best possible 5-card hand from the hole and community cards.
    
    Returns:
        A tuple containing:
        - best_rank (int): The rank of the best hand (0-8).
        - best_kicker_values (List[int]): The kicker values for tie-breaking.
        - hand_name (str): The chinese name of the hand.
        - best_hand_cards (List[Card]): The 5 cards that form the best hand, sorted by value.
    """
    all_cards = hole + community

    # Before the flop, just high card.
    if len(community) < 3:
        sorted_hole = sorted(hole, key=lambda c: c.value(), reverse=True)
        best_rank = 0
        best_kicker_values = [c.value() for c in sorted_hole]
        hand_name = get_hand_name(best_rank, best_kicker_values)
        return (best_rank, best_kicker_values, hand_name, sorted_hole)

    best_rank = -1
    best_kicker_values = []
    best_hand_cards: List[Card] = []

    # Iterate through all 5-card combinations from the 7 total cards
    for combo_cards in itertools.combinations(all_cards, 5):
        rank, kicker_values = _evaluate_five(list(combo_cards))
        
        # Compare with the best hand found so far
        current_hand_tuple = (rank, kicker_values)
        best_hand_tuple = (best_rank, best_kicker_values)

        if current_hand_tuple > best_hand_tuple:
            best_rank = rank
            best_kicker_values = kicker_values
            best_hand_cards = list(combo_cards)

    hand_name = get_hand_name(best_rank, best_kicker_values)

    # Sort the final best hand for clear presentation
    sorted_best_hand = sorted(best_hand_cards, key=lambda c: c.value(), reverse=True)

    return (best_rank, best_kicker_values, hand_name, sorted_best_hand)


def _evaluate_five(cards: List[Card]) -> Tuple[int, List[int]]:
    """
    Evaluates a single 5-card hand.

    Returns:
        A tuple containing:
        - rank (int): The rank of the hand (0-8).
        - kicker_values (List[int]): The kicker values for tie-breaking.
    """
    values = sorted([c.value() for c in cards], reverse=True)
    suits = [c.suit for c in cards]
    value_counts = Counter(values)
    is_flush = len(set(suits)) == 1
    
    # A-5 straight check (values are [12, 3, 2, 1, 0])
    is_wheel = (values == [12, 3, 2, 1, 0])
    # General straight check
    is_straight = (len(set(values)) == 5 and (max(values) - min(values) == 4)) or is_wheel

    if is_straight and is_flush:
        # For wheel (A-5 straight), kicker is 5,4,3,2,A -> values are 3,2,1,0,-1 (A is low)
        kicker = [3, 2, 1, 0, -1] if is_wheel else values
        return (8, kicker)
    
    if 4 in value_counts.values():
        four_val = [v for v, c in value_counts.items() if c == 4][0]
        kicker_val = [v for v, c in value_counts.items() if c == 1][0]
        return (7, [four_val, kicker_val])

    if sorted(value_counts.values()) == [2, 3]:
        three_val = [v for v, c in value_counts.items() if c == 3][0]
        pair_val = [v for v, c in value_counts.items() if c == 2][0]
        return (6, [three_val, pair_val])

    if is_flush:
        return (5, values)

    if is_straight:
        kicker = [3, 2, 1, 0, -1] if is_wheel else values
        return (4, kicker)

    if 3 in value_counts.values():
        three_val = [v for v, c in value_counts.items() if c == 3][0]
        kickers = sorted([v for v in values if v != three_val], reverse=True)
        return (3, [three_val] + kickers)

    if list(value_counts.values()).count(2) == 2:
        pairs_vals = sorted([v for v, c in value_counts.items() if c == 2], reverse=True)
        kicker_val = [v for v, c in value_counts.items() if c == 1][0]
        return (2, pairs_vals + [kicker_val])

    if 2 in value_counts.values():
        pair_val = [v for v, c in value_counts.items() if c == 2][0]
        kickers = sorted([v for v in values if v != pair_val], reverse=True)
        return (1, [pair_val] + kickers)
    
    return (0, values)
