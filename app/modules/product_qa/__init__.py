"""Product Q&A — phase 3 of the reviews initiative.

One question → many answers shape. Open to any authenticated customer
(no verified-purchase gate) so pre-purchase queries are possible.
Sellers linked to the product's seller_id can answer with the
``is_seller_answer`` badge automatically attached. Same admin
moderation pipeline as reviews (pending → approved / rejected, with
disable/reenable for already-approved rows).

Phase-3 scope:
  * ``product_questions`` + ``product_answers`` tables
  * Public list of approved questions + answers
  * Customer create question + answer + helpful-vote
  * Admin moderation surface
  * Seller answer badge

Out of scope (later phases):
  * AI auto-moderation (Reviews phase 4)
  * Question-level subscription (notify on new answer)
  * Best-answer highlighting / sort
"""
