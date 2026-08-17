"""Small independent oracle for the canonical fixtures.

This is deliberately incomplete. It validates the primitive geometry used by
the fixtures; it is not a production Chanlun implementation.
"""


def classify_fractal(triple):
    """Return 'top', 'bottom', or 'none' for three processed K-lines."""
    if len(triple) != 3:
        raise ValueError("exactly three processed K-lines are required")
    left, middle, right = triple
    if (
        middle["high"] > left["high"]
        and middle["high"] > right["high"]
        and middle["low"] > left["low"]
        and middle["low"] > right["low"]
    ):
        return "top"
    if (
        middle["high"] < left["high"]
        and middle["high"] < right["high"]
        and middle["low"] < left["low"]
        and middle["low"] < right["low"]
    ):
        return "bottom"
    return "none"


def intervals_include(a, b):
    return (
        a["high"] >= b["high"] and a["low"] <= b["low"]
    ) or (
        b["high"] >= a["high"] and b["low"] <= a["low"]
    )


def merge_inclusion(a, b, direction):
    """Merge one containing pair and retain raw extreme source IDs."""
    if not intervals_include(a, b):
        raise ValueError("K-lines do not have an inclusion relationship")
    if direction == "up":
        high_item = max((a, b), key=lambda item: item["high"])
        low_item = max((a, b), key=lambda item: item["low"])
    elif direction == "down":
        high_item = min((a, b), key=lambda item: item["high"])
        low_item = min((a, b), key=lambda item: item["low"])
    else:
        raise ValueError("direction must be 'up' or 'down'")
    return {
        "high": high_item["high"],
        "low": low_item["low"],
        "members": [a["id"], b["id"]],
        "high_source": high_item["id"],
        "low_source": low_item["id"],
    }


def reduce_same_type(first, second):
    """Return the retained candidate for two adjacent same-type fractals."""
    if first["type"] != second["type"]:
        raise ValueError("fractal types differ")
    if first["type"] == "top":
        return second if second["price"] > first["price"] else first
    if first["type"] == "bottom":
        return second if second["price"] < first["price"] else first
    raise ValueError("unknown fractal type")


def endpoint_members_overlap(first, second):
    return bool(set(first["members"]) & set(second["members"]))

