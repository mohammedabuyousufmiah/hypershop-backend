"""Customer-facing e-commerce wallet module.

Distinct from the rider_wallet (rider COD ledger) and seller_wallet
(marketplace seller ledger).

Tables: hypershop_wallets, hypershop_wallet_txns. See migration
``0059_hypershop_wallet`` for the schema.

Use cases:
  * refund credit (when a return is approved, credit the wallet)
  * gift-card redemption (gift_cards.redeem credits the wallet)
  * loyalty redemption (in future, when loyalty.redeem credits to wallet)
  * checkout debit (apply wallet balance toward an order total)
"""
