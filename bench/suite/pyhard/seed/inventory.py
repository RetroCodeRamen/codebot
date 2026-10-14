class Inventory:
    def __init__(self):
        self.items = {}

    def add_item(self, sku, qty, price):
        self.items[sku] = {"qty": qty, "price": price}

    def remove_item(self, sku, qty):
        self.items[sku]["qty"] -= qty
        if self.items[sku]["qty"] == 0:
            del self.items[sku]

    def total_value(self):
        return sum(i["qty"] * i["price"] for i in self.items.values())
