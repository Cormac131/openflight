/*
 * OpenFlight IWR6843 firmware identity, reported by the CLI "stats version" sub-mode.
 *
 * firmware/Makefile stamps all three: L3_FW_VERSION from firmware/VERSION
 * (bump it with `make -C firmware bump-version PART=patch|minor|major`),
 * L3_FW_GIT with the source commit and L3_FW_BUILT with a UTC timestamp.
 * A bare out-of-tree build reports the fallbacks below.
 *
 * The host parses the reply as space-separated key=value pairs
 * (src/openflight/iwr6843/firmware_version.py, parse_version_reply), so values must
 * not contain spaces.
 */
#ifndef L3_FW_VERSION_H
#define L3_FW_VERSION_H

#ifndef L3_FW_VERSION
#define L3_FW_VERSION "0.0.0-dev"
#endif

#ifndef L3_FW_GIT
#define L3_FW_GIT "unknown"
#endif

#ifndef L3_FW_BUILT
#define L3_FW_BUILT "unknown"
#endif

#ifdef HYBRID_CADENCE_CAPTURE
#define L3_FW_VARIANT "hybrid-cadence"
#else
#define L3_FW_VARIANT "configurable-snapshot"
#endif

#endif /* L3_FW_VERSION_H */
