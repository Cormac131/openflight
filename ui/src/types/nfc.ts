/** A learned NFC tag mapping, as persisted by the server. */
export interface ClubTag {
  uid: string;
  /** UID grouped for display, e.g. "04:A2:B1:C3". */
  uid_display: string;
  club: string;
  learned_at: string;
  last_seen_at: string | null;
}

/** One tag presentation reported by the reader. */
export interface NfcScan {
  uid: string;
  uid_display: string;
  timestamp: number;
  club: string | null;
  known: boolean;
  /** Where the club came from: the tag's own record, or this rig's registry. */
  source: 'tag' | 'registry' | null;
  /** True when the tag's memory has never been written. */
  blank: boolean;
  /** True when the tag's NDEF memory could be read. */
  writable: boolean;
}

export interface ClubTagsPayload {
  tags: ClubTag[];
  /** True when the PN532 opened and the reader thread is running. */
  enabled: boolean;
  /** True when `--nfc` was requested, even if the reader failed to start. */
  requested?: boolean;
  /** Reader init failure, when `--nfc` was requested but the PN532 is down. */
  error?: string | null;
}
