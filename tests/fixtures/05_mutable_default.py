"""Классическая ошибка с мутабельным дефолтным аргументом."""
def add_to_cart(item, cart=[]):
    cart.append(item)
    return cart
