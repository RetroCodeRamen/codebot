import sys
sys.path.insert(0, ".")
f = []
try:
    from inventory import Inventory
    i = Inventory(); i.add_item("ab", 2, 1.10); i.add_item("AB", 3, 1.20)
    if list(i.items) != ["AB"]: f.append(f"skus must be normalised to upper case and merged: items are {list(i.items)!r}")
    elif i.items["AB"]["qty"] != 5: f.append(f"adding the same sku twice must ADD the quantity: qty is {i.items['AB']['qty']!r}, expected 5")
    elif i.items["AB"]["price"] != 1.20: f.append(f"price must become the latest price: {i.items['AB']['price']!r}")
    if i.total_value() != 6.0: f.append(f"total_value() is {i.total_value()!r}, expected 6.0 (5 * 1.2, rounded to 2 decimals)")
    j = Inventory(); j.add_item("x", 3, 0.1)
    if j.total_value() != 0.3: f.append(f"total_value() must be rounded to 2 decimals: got {j.total_value()!r}, expected 0.3")
    try: j.remove_item("nope", 1); f.append("remove_item of an unknown sku must raise KeyError")
    except KeyError: pass
    try: j.remove_item("x", 9); f.append("removing more than in stock must raise ValueError")
    except ValueError as e:
        if "insufficient stock" not in str(e): f.append(f"the ValueError message must be 'insufficient stock', got {str(e)!r}")
    j.remove_item("x", 3)
    if "X" in j.items or j.items: f.append(f"removing the whole quantity must delete the item: items are {j.items!r}")
except Exception as e: f.append(f"raised {e!r}")
print("\n".join(f)); sys.exit(1 if f else 0)
