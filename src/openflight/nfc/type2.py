"""NFC Forum Type 2 tag (MIFARE Ultralight / NTAG) layout.

Shared by every reader driver that inventories these tags: the raw READ
command, where the capability container lives, and how many bytes of user
memory are worth pulling for a club record. The PN532 and PN5180 drivers
both reach ISO14443A Type 2 tags this way, over otherwise unrelated host
protocols, so the tag-side layout is factored out here instead of being
declared twice.
"""

from __future__ import annotations

# SEL_RES (SAK) that marks an NFC Forum Type 2 tag during ISO14443A
# anticollision, as opposed to MIFARE Classic and other card types this
# driver can identify by UID but not read.
TYPE2_SAK = 0x00

TYPE2_READ = 0x30
TYPE2_PAGE_BYTES = 4
TYPE2_READ_BYTES = 16
CAPABILITY_CONTAINER_PAGE = 3
CAPABILITY_CONTAINER_MAGIC = 0xE1
FIRST_DATA_PAGE = 4
# A club record is around twenty bytes. Reading the first four pages-worth of
# user memory finds its TLV without waiting on a full 144-byte NTAG213 dump.
NDEF_SCAN_BYTES = 64
