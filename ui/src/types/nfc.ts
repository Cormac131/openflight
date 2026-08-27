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
}

export interface ClubTagsPayload {
  tags: ClubTag[];
  enabled: boolean;
}
