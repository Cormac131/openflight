"""l3_dump.c wires the tee band, the range-only impact and the impact fit the
way firmware_replay does. The board file needs TI headers, so this reads its
source; the behaviour is tested through the replay (test_iwr6843_firmware_replay*).

Band off (bandBins 0, the default) must be exactly the board's old behaviour:
the club track reads the trigger's region, the ball is armed at the tee and
the post-impact targets are unfiltered. Band on: before impact the club track
reads the whole window, keeping only targets short of the band
(firmware_replay._pre_impact_club_targets); after impact the band is dropped
from the targets (l3_band_filter). The band is placed on the noisiest idle
bins near the destination (l3_band_place over gBandNoise) and frozen while a
club track is active."""

from __future__ import annotations

import re

from openflight.iwr6843.firmware_host import FIRMWARE_DIR

SOURCE = (FIRMWARE_DIR / "l3_dump.c").read_text(encoding="utf-8")


def body(name: str) -> str:
    """The body of the static function ``name`` (its definition, not a
    forward declaration: only the definition is followed by a brace)."""
    match = re.search(rf"static \w+ {name}\([^)]*\)\s*\{{(.*?)\n\}}", SOURCE, re.S)
    assert match, name
    return match.group(1)


def test_globals_for_the_band_the_range_impact_and_the_fit():
    for declaration in (
        "static l3_impact_fit_cfg_t gImpactFitCfg;",
        "static l3_band_t           gBand;",
        "static l3_impact_t         gRangeImpact;",
        "static l3_impact_fit_t     gImpactFit;",
        "static l3_band_noise_t     gBandNoise;",
        "static uint8_t             gBandFrozen;",
    ):
        assert declaration in SOURCE, declaration
    assert '#include "l3_band.h"' in SOURCE
    assert '#include "l3_impact_fit.h"' in SOURCE


def test_band_off_is_no_band_on_every_pre_impact_frame():
    self_trigger = body("l3_considerSelfTrigger")
    enabled = self_trigger.index("if (gImpactFitCfg.bandBins > 0.0F) {")
    off = self_trigger.index("gBand.valid = 0U;")
    assert enabled < off < self_trigger.index("l3_preImpactClubTargets(")


def test_noise_map_is_updated_only_from_idle_whole_window_frames():
    """After the club track update: an active track freezes the band; an idle
    frame thaws it and feeds the whole window it scored to the noise map."""
    self_trigger = body("l3_considerSelfTrigger")
    track = self_trigger.index("appended = l3_track_update(&gClubTrack,")
    active = self_trigger.index("if (gClubTrack.active) {", track)
    frozen = self_trigger.index("gBandFrozen = 1U;", active)
    thawed = self_trigger.index("gBandFrozen = 0U;", frozen)
    scored = self_trigger.index("if (windowCount > 0U) {", thawed)
    update = self_trigger.index(
        "l3_band_noise_update(&gBandNoise, gTrigCfg.stat, frame.binStart, obs,", scored
    )
    assert track < active < frozen < thawed < scored < update


def test_noise_map_is_reset_once_with_the_fit_defaults():
    """The map persists across shots: reset only where the fit's defaults are
    set once, never at rearm."""
    ensure = body("l3_ensureRadarCal")
    defaults = ensure.index("l3_impact_fit_cfg_defaults(&gImpactFitCfg);")
    assert ensure.index("l3_band_noise_reset(&gBandNoise);") > defaults
    assert SOURCE.count("l3_band_noise_reset(") == 1
    assert "l3_band_noise_reset" not in body("l3_trigRearm")


def test_pre_impact_club_targets_keep_only_short_of_a_valid_band():
    helper = body("l3_preImpactClubTargets")
    valid = helper.index("if (gImpactFitCfg.bandBins > 0.0F) {")
    whole = helper.index("l3_verticalResidual(frame, bin, NULL, &obs[bin]);")
    keep = helper.index("l3_band_keep_short(&gBand, targets, found)")
    region = helper.index("return l3_obs_extract(params, frameIndex, frameUs, regionFirstBin,")
    assert valid < whole < keep < region, "the whole window only when the band is enabled"
    # The whole-window count is reported for the noise map; the trigger view none.
    assert "*windowCount = count;" in helper
    assert "*windowCount = 0U;" in helper
    # The whole window: global first bin frame->binStart, so a target's
    # peakBin - frame.binStart is its local bin in both modes.
    assert "l3_obs_extract(params, frameIndex, frameUs, frame->binStart, obs, count," in helper
    assert "l3_band_filter" not in helper, "before impact: short of the band, not merely outside"


def test_club_track_reads_the_helper_and_the_trigger_keeps_its_region():
    self_trigger = body("l3_considerSelfTrigger")
    trig = self_trigger.index("l3_trig_observe(&gTrig, gPreFramesCaptured, teeBin,")
    helper = self_trigger.index(
        "found = l3_preImpactClubTargets(&frame, obs, frame.binStart + first,"
    )
    track = self_trigger.index("appended = l3_track_update(&gClubTrack, targets, found,")
    assert trig < helper < track
    assert "l3_obs_extract(" not in self_trigger, "the club's targets come from the helper only"
    assert "l3_channelSnapshot(&frame, (uint32_t)hit->peakBin - frame.binStart," in self_trigger


def test_range_impact_runs_every_pre_impact_frame_and_feeds_the_shot():
    self_trigger = body("l3_considerSelfTrigger")
    delivery = self_trigger.index("(void)l3_track_delivery(&gClubTrack, 8U, &gDelivery);")
    club_in = self_trigger.index(
        "l3_impact_fit_track(&gImpactFitCfg, L3_FIT_CLUB_IN, l3_fit_span_point,"
    )
    ranged = self_trigger.index("ranged = l3_impact_update_range(&gRangeImpact, &clubIn,")
    assert delivery < club_in < ranged
    # The range-only impact is the self-trigger: it feeds the shot, then freezes.
    observe_call = self_trigger.index("l3_shotObserve(teeBin, ranged);")
    assert ranged < observe_call < self_trigger.index("if (!ranged) {")
    observe = body("l3_shotObserve")
    assert "in.rangeFired = (uint8_t)(ranged ? 1U : 0U);" in observe
    assert "in.impactTimestampUs = gRangeImpact.impactTimestampUs;" in observe


def test_post_impact_targets_are_band_filtered():
    ball_track = body("l3_considerBallTrack")
    extract = ball_track.index("found = l3_obs_extract(&params, frameIndex, gPostTimestampUs,")
    band = ball_track.index("found = l3_band_filter(&gBand, targets, found);")
    follow = ball_track.index("l3_track_follow(&gClubTrack, targets, found,")
    assert extract < band < follow


def test_ball_tracker_is_armed_at_the_band_edge():
    assert "return gBand.valid ? gBand.hiBin : (float)teeBin;" in body("l3_ballArmBin")
    assert "l3_ball_track_arm(&gBallTrack, l3_ballArmBin(teeBin)," in body("l3_shotObserve")


def test_impact_fit_runs_before_the_result_is_built():
    ball_track = body("l3_considerBallTrack")
    assert ball_track.index("l3_impactFitRun();") < ball_track.index("l3_result_build(")
    run = body("l3_impactFitRun")
    assert "l3_fit_span_after(&gClubTrack, gShot.impactFrame, &clubOut);" in run
    assert "l3_impact_fit_run(&gImpactFitCfg, &clubIn, &clubOut, &ballOut," in run
    assert "gShot.impactTimestampUs = l3_round_us(gImpactFit.impactUs);" in run


def test_rearm_forgets_the_range_impact_and_the_fit():
    rearm = body("l3_trigRearm")
    assert "l3_impact_rearm(&gRangeImpact);" in rearm
    assert "l3_impact_fit_reset(&gImpactFit);" in rearm


def test_fit_cfg_defaults_once_so_a_configured_band_persists():
    """trackCfg impactFit may come before or after triggerCfg/sensorStart:
    only the first l3_ensureRadarCal sets the defaults."""
    ensure = body("l3_ensureRadarCal")
    assert "if (!gImpactFitCfgSet) {" in ensure
    assert ensure.index("if (!gImpactFitCfgSet) {") < ensure.index(
        "l3_impact_fit_cfg_defaults(&gImpactFitCfg);"
    )
    assert SOURCE.count("l3_impact_fit_cfg_defaults(") == 1
    configure = body("l3_clubTrackConfigure")
    assert "gImpactFitCfg.binWidthM = cfg.binWidthM;" in configure
    assert "l3_impact_init(&gRangeImpact, &gImpactCfg);" in configure
    assert "l3_impact_init(&gRangeImpact, &gImpactCfg);" in body("l3_cli_trackCfgImpact")


def test_band_command_is_a_track_cfg_sub_mode():
    """The CLI table is at the SDK's CLI_MAX_CMD: a sub-mode, not a new command."""
    track_cfg = body("l3_cli_trackCfg")
    assert 'strcmp(argv[1], "impactFit") == 0' in track_cfg
    assert "return l3_cli_trackCfgImpactFit(argc, argv);" in track_cfg
    handler = body("l3_cli_trackCfgImpactFit")
    assert "l3_parseFloats(argc, argv, 2, 1U, values) != 0" in handler
    assert "!(values[0] >= 0.0F)" in handler, "negative and NaN refused"
    assert "values[0] > L3_IMPACT_FIT_MAX_BAND_BINS" in handler
    assert handler.index("l3_ensureRadarCal();") < handler.index(
        "gImpactFitCfg.bandBins = values[0];"
    )
    assert 'CLI_write("Done\\n");' in handler
    assert "#define L3_IMPACT_FIT_MAX_BAND_BINS 64.0F" in SOURCE
    assert "tableEntry[19]" not in SOURCE


def test_track_log_prints_the_impact_fit_after_the_impact():
    log = body("l3_cli_triggerLog")
    assert log.index("l3_impact_format(&gRangeImpact, line, sizeof(line));") < log.index(
        "l3_impact_fit_format(&gImpactFit, line, sizeof(line));"
    )


def test_new_modules_are_in_the_board_image():
    makefile = (FIRMWARE_DIR / "makefile").read_text(encoding="utf-8")
    assert "l3_band.c" in makefile and "l3_impact_fit.c" in makefile


def test_post_impact_ball_runs_before_the_club_which_gets_the_scene():
    ball_track = body("l3_considerBallTrack")
    ball = ball_track.index("l3_ball_track_update_joint(&gBallTrack")
    club = ball_track.index("l3_track_follow(&gClubTrack")
    assert ball < club
    assert "L3_TRACK_NO_TARGET" in ball_track[ball:club]
    assert "&follow)" in ball_track[club : club + 200]
    assert "l3_track_recent_rate(&gBallTrack.core)" in ball_track
    assert "gBallTrack.lastTargetIndex" in ball_track


def test_post_impact_unknown_approach_falls_back_to_the_club_ceiling():
    ball_track = body("l3_considerBallTrack")
    assert "L3_TRACK_FOLLOW_UNKNOWN_APPROACH_MPS" in ball_track
    assert "gShot.delivery.speedValid" in ball_track


def test_board_places_the_band_from_the_noise_map_until_frozen():
    self_trigger = body("l3_considerSelfTrigger")
    place = self_trigger.index("l3_band_place(&gBandNoise,")
    targets = self_trigger.index("l3_preImpactClubTargets(")
    assert place < targets
    assert "gBandFrozen" in self_trigger[: place + 200]
    assert "l3_band_noise_update(&gBandNoise," in self_trigger
    assert "gBandFrozen = 0U" in body("l3_trigRearm")
    assert "l3_band_around" not in SOURCE


def test_ball_snr_is_a_track_cfg_sub_mode():
    """The ball tracker's extraction snr is set apart from the trigger's."""
    track_cfg = body("l3_cli_trackCfg")
    assert 'strcmp(argv[1], "ballSnr") == 0' in track_cfg
    assert "return l3_cli_trackCfgBallSnr(argc, argv);" in track_cfg
    handler = body("l3_cli_trackCfgBallSnr")
    assert "l3_parseFloats(argc, argv, 2, 1U, values) != 0" in handler
    # 0 restores the firmware default; otherwise at least the floor. NaN refused.
    assert "!(values[0] == 0.0F || values[0] >= 1.0F)" in handler
    assert "gBallSnr = values[0];" in handler
    assert "static float               gBallSnr;" in SOURCE


def test_ball_extraction_uses_the_configured_ball_snr_else_the_default():
    ball_track = body("l3_considerBallTrack")
    assert "params.snr = (gBallSnr > 0.0F) ? gBallSnr : gBallTrackCfg.snr;" in ball_track


def test_ball_angles_take_the_ball_tracks_rate_for_the_tdm_branch():
    ball = body("l3_considerBallTrack")
    assert (
        "float rateMps = l3_track_recent_rate(&gBallTrack.core) * gBallTrack.core.cfg.binWidthM;"
        in ball
    )
    # the measured lag-1 phase stays the rotor; the fitted rate is the radial velocity
    flat = " ".join(ball.split())
    assert "hit->dopplerPhaseRad, (rateMps != 0.0F) ? rateMps : newest.radialVelocityMps" in flat
    assert "continuousTdm" not in SOURCE


def test_hypothesis_angles_take_their_fitted_rate_for_the_tdm_branch():
    ball = body("l3_considerBallTrack")
    hyps = ball[ball.index("l3_ball_hyp_fit(") :]
    assert "hit->dopplerPhaseRad, radial, &snapshot);" in hyps


def test_every_launch_reset_carries_the_no_late_sentinel():
    """A zeroed lateFrom reads as the late fit from track point 0, so each
    memset of gLaunch is followed by the sentinel."""
    resets = [m.end() for m in re.finditer(r"memset\(&gLaunch, 0, sizeof\(gLaunch\)\);", SOURCE)]
    assert len(resets) >= 2
    for end in resets:
        assert SOURCE[end:].lstrip().startswith("gLaunch.lateFrom = L3_LAUNCH_NO_LATE;")


def test_track_cfg_cal_and_elem_survive_trigger_cfg_and_sensor_start():
    """Only l3_ensureRadarCal initialises gRadarCal, and only
    when it has never been set; nothing else overwrites it."""
    assert SOURCE.count("l3_cal_identity(&gRadarCal") == 1
    ensure = body("l3_ensureRadarCal")
    assert "if (gRadarCal.virtualElements == 0U) {" in ensure
    for handler in ("l3_cli_triggerCfg", "l3_cli_sensorStart"):
        assert "gRadarCal =" not in body(handler)
        assert "memset(&gRadarCal" not in body(handler)
