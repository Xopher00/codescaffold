"""NORMALIZED pair member B — same logic as normalized_a after renaming locals."""


def factorial(num: int) -> int:
    # iterative product over the range
    acc = 1
    for x in range(1, num + 1):
        acc *= x
    return acc
