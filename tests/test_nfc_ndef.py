"""Tests for the NDEF text-record codec used by club tags."""

import pytest

from openflight.nfc import ndef
from openflight.nfc.ndef import NdefError


class TestTextRecords:
    def test_round_trip(self):
        assert ndef.decode_text_record(ndef.encode_text_record("7-iron")) == "7-iron"

    def test_encoding_matches_the_ndef_text_record_layout(self):
        record = ndef.encode_text_record("pw")

        # MB|ME|SR|TNF=1, type length 1, payload length, 'T', status, "en", text.
        assert record == bytes([0xD1, 0x01, 0x05, 0x54, 0x02]) + b"enpw"

    def test_a_phone_written_record_in_another_language_still_reads(self):
        record = ndef.encode_text_record("driver", language="fr-CA")

        assert ndef.decode_text_record(record) == "driver"

    def test_non_ascii_text_survives(self):
        assert ndef.decode_text_record(ndef.encode_text_record("hierro-7")) == "hierro-7"

    def test_a_uri_record_is_not_read_as_text(self):
        # TNF well-known, type 'U': somebody else's tag, not ours to interpret.
        uri = bytes([0xD1, 0x01, 0x05, 0x55, 0x00]) + b"a.com"

        assert ndef.decode_text_record(uri) is None

    def test_a_utf16_record_is_declined(self):
        record = bytearray(ndef.encode_text_record("driver"))
        record[4] |= 0x80

        assert ndef.decode_text_record(bytes(record)) is None

    def test_a_truncated_record_is_declined(self):
        assert ndef.decode_text_record(ndef.encode_text_record("driver")[:6]) is None

    def test_an_empty_message_is_declined(self):
        assert ndef.decode_text_record(b"") is None

    def test_bytes_after_a_complete_record_are_declined(self):
        extra = bytes([0x51, 0x01, 0x05, 0x55, 0x00]) + b"a.com"
        message = ndef.encode_text_record("driver") + extra

        assert ndef.decode_text_record(message) is None

    def test_trailing_padding_after_a_complete_record_is_declined(self):
        assert ndef.decode_text_record(ndef.encode_text_record("driver") + bytes(8)) is None

    def test_a_second_text_record_is_declined(self):
        message = ndef.encode_text_record("driver") + ndef.encode_text_record("pw")

        assert ndef.decode_text_record(message) is None

    def test_a_well_formed_multi_record_message_is_declined(self):
        record = bytearray(ndef.encode_text_record("driver"))
        record[0] &= ~0x40
        extra = bytes([0x51, 0x01, 0x05, 0x55, 0x00]) + b"a.com"

        assert ndef.decode_text_record(bytes(record) + extra) is None

    def test_an_oversized_payload_is_refused(self):
        with pytest.raises(NdefError):
            ndef.encode_text_record("x" * 300)

    def test_an_unusable_language_code_is_refused(self):
        with pytest.raises(NdefError):
            ndef.encode_text_record("driver", language="")


class TestTlvFraming:
    def test_wrap_and_find_round_trip(self):
        message = ndef.encode_text_record("7-iron")

        assert ndef.find_ndef_message(ndef.wrap_tlv(message)) == message

    def test_trailing_bytes_after_the_terminator_are_ignored(self):
        message = ndef.encode_text_record("gw")
        memory = ndef.wrap_tlv(message) + bytes(32)

        assert ndef.find_ndef_message(memory) == message

    def test_leading_null_tlvs_are_skipped(self):
        message = ndef.encode_text_record("sw")
        memory = bytes([ndef.TLV_NULL, ndef.TLV_NULL]) + ndef.wrap_tlv(message)

        assert ndef.find_ndef_message(memory) == message

    def test_an_ndef_tlv_after_a_proprietary_one_is_found(self):
        message = ndef.encode_text_record("lw")
        memory = bytes([ndef.TLV_PROPRIETARY, 0x02, 0xAA, 0xBB]) + ndef.wrap_tlv(message)

        assert ndef.find_ndef_message(memory) == message

    def test_a_three_byte_length_is_understood(self):
        message = b"x" * 300
        memory = bytes([ndef.TLV_NDEF, 0xFF, 0x01, 0x2C]) + message + bytes([ndef.TLV_TERMINATOR])

        assert ndef.find_ndef_message(memory) == message

    def test_a_long_message_is_written_with_a_three_byte_length(self):
        wrapped = ndef.wrap_tlv(b"y" * 260)

        assert wrapped[:4] == bytes([ndef.TLV_NDEF, 0xFF, 0x01, 0x04])

    def test_a_terminator_before_any_ndef_means_nothing_is_stored(self):
        assert ndef.find_ndef_message(bytes([ndef.TLV_TERMINATOR])) is None

    def test_a_tlv_claiming_more_than_it_holds_is_refused(self):
        assert ndef.find_ndef_message(bytes([ndef.TLV_NDEF, 0x20, 0x01, 0x02])) is None


class TestClassifyingTagMemory:
    def test_factory_fresh_memory_is_blank(self):
        assert ndef.read_tag_content(bytes(64)).blank is True

    def test_empty_memory_is_blank(self):
        assert ndef.read_tag_content(b"").blank is True

    def test_a_formatted_but_empty_tag_is_blank(self):
        # Some vendors ship an NDEF TLV of length zero.
        content = ndef.read_tag_content(bytes([ndef.TLV_NDEF, 0x00, ndef.TLV_TERMINATOR]))

        assert content.blank is True
        assert content.text is None

    def test_a_club_tag_reads_back_its_club(self):
        memory = ndef.wrap_tlv(ndef.encode_text_record("7-iron")) + bytes(32)
        content = ndef.read_tag_content(memory)

        assert content.text == "7-iron"
        assert content.blank is False
        assert content.foreign is False

    def test_a_tag_holding_a_uri_is_foreign_not_blank(self):
        uri = bytes([0xD1, 0x01, 0x05, 0x55, 0x00]) + b"a.com"
        content = ndef.read_tag_content(ndef.wrap_tlv(uri))

        assert content.blank is False
        assert content.text is None
        assert content.foreign is True

    def test_a_club_record_followed_by_another_record_is_foreign(self):
        extra = bytes([0x51, 0x01, 0x05, 0x55, 0x00]) + b"a.com"
        memory = ndef.wrap_tlv(ndef.encode_text_record("driver") + extra)
        content = ndef.read_tag_content(memory)

        assert content.text is None
        assert content.blank is False
        assert content.foreign is True

    def test_written_but_unformatted_memory_is_foreign(self):
        content = ndef.read_tag_content(bytes([0x11, 0x22, 0x33, 0x44]))

        assert content.blank is False
        assert content.foreign is True
