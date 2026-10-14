class Inventory:
    def __init__(self):
        self.items = {}

    def add_item(self, sku, qty, price):
        sku = sku.upper()
        if sku in self.items:
            self.items[sku]["qty"] += qty
            self.items[sku]["price"] = price
        else:
            self.items[sku] = {"qty": qty, "price": price}

    def remove_item(self, sku, qty):
        sku = sku.upper()
        if sku not in self.items:
            raise KeyError(f"unknown sku {sku}")
        if qty > self.items[sku]["qty"]:
            raise ValueError("insufficient stock")
        self.items[sku]["qty"] -= qty
        if self.items[sku]["qty"] == 0:
            del self.items[sku]

    def total_value(self):
        return round(sum(i["qty"] * i["price"] for i in self.items.values()), 2)
