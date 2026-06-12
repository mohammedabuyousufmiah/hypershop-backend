"""0062 agent_softphone — per-agent SIP credentials on cc_agent_profile.

Adds two nullable columns:
  sip_extension       VARCHAR(32)  — agent's extension on the SBC,
                                     e.g. "agent42". Unique per agent.
  sip_password_enc    VARCHAR(256) — SIP REGISTER password. Plaintext
                                     for now (rotated frequently; SBC
                                     extension passwords aren't reused
                                     anywhere else). If/when we need
                                     at-rest encryption, switch this
                                     column to ciphertext + add a kdf
                                     wrapper at the read endpoint.

Provisioning lifecycle:
  1. Telephony admin creates the extension on Banglalink HUB's portal.
  2. Hypershop admin PUTs the extension + password via
     ``PUT /admin/customer-care/agents/{user_id}/softphone``.
  3. Agent calls ``GET /customer-care/me/softphone`` from the
     admin-panel softphone widget to fetch their creds at login time.

Rotation: re-PUT with a fresh password; existing browser sessions get
a 401 from the SBC on next REGISTER attempt and re-fetch.

Storage caveat: the column name carries ``_enc`` for forward-compat
even though the value is plaintext today — renaming a column is a
destructive migration we want to avoid, so the name reserves the
encryption upgrade path.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0062_agent_softphone"
down_revision: str | Sequence[str] | None = "0061_voice_calls"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "cc_agent_profile",
        sa.Column("sip_extension", sa.String(32), nullable=True),
    )
    op.add_column(
        "cc_agent_profile",
        sa.Column("sip_password_enc", sa.String(256), nullable=True),
    )
    # Extensions are unique across agents on the SBC. Partial unique
    # index so the NULL state (un-provisioned agents) doesn't collide.
    op.create_index(
        "uq_cc_agent_profile_sip_extension",
        "cc_agent_profile",
        ["sip_extension"],
        unique=True,
        postgresql_where=sa.text("sip_extension IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_cc_agent_profile_sip_extension",
        table_name="cc_agent_profile",
    )
    op.drop_column("cc_agent_profile", "sip_password_enc")
    op.drop_column("cc_agent_profile", "sip_extension")
