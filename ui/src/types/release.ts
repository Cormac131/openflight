/** Mirrors `ReleaseInfo.to_dict()` in `src/openflight/release.py`. */

export type ReleaseChannel = 'stable' | 'experimental' | 'source';

export interface ReleaseInfo {
  format_version: number;
  version: string;
  base_version: string;
  channel: ReleaseChannel;
  tag: string | null;
  commit: string | null;
  built_at: string | null;
  repository: string | null;
}
