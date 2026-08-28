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
  /** True when the reader can write a club onto this tag. */
  writable: boolean;
}

/** Result of writing a club onto a tag. */
export interface ClubTagWrite {
  state: 'written' | 'failed';
  uid?: string;
  club?: string;
  error?: string;
}

/** Where the blank-tag write flow has got to. */
export type WriteStage = 'select' | 'confirm' | 'writing' | 'failed';

export interface ClubTagsPayload {
  tags: ClubTag[];
  enabled: boolean;
}
