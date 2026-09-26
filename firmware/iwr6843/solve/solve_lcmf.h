/* LCMF-v1 vertical launch-angle stage for the DSS solve -- SCOPED PORT.
 *
 * Port of src/openflight/iwr6843/lcmf.py's estimate_lcmf_v1() (lcmf.py:766),
 * restricted to the channel-model vertical launch-angle decision path. See
 * solve_lcmf.c's file banner for the full audit and exactly what is and is
 * not covered -- this header states only the resulting contract.
 *
 * PORTED (affects angle_deg / channels_used / single_channel /
 * component_std_deg / status, i.e. the actual accept/reject decision):
 *   - the early rejects (no track / reject quality / missing TDM sign)
 *   - _snapshot_cache (doa.canonicalize_tx_blocks, Calibration.apply,
 *     Calibration.true_range -- this is where a calibration bug would hide)
 *   - _balanced_indices
 *   - _spatial_dictionary (two8, four4_path_tdm) and
 *     multipath.ballistic_trajectory_from_range
 *   - multipath.leave_one_channel_out_error (complex pseudo-inverse via
 *     normal equations)
 *   - _frame_objective, _refine_grid, grid_curvature
 *   - measured_channels, combine_channels
 *
 * NOT PORTED (read the traced control flow in lcmf.py before assuming this
 * matters -- it provably does not, for the fields above):
 *   - _fast_estimates/_prepared_fft/_fast_design/_fit_error/_quadratic_peak
 *     (the "fast_*" models). These are diagnostic-only: estimate_lcmf_v1
 *     computes raw_angle_deg/channels_used/single_channel from
 *     channel_components alone (lcmf.py:921), BEFORE the fast estimates are
 *     even computed (lcmf.py:899-911) and folds them into components_deg
 *     only for session-log diagnostics. Confirmed empirically across all 18
 *     corpus cases: every case where the fast stage would run either never
     * reaches it (rejected earlier) or the fast stage never raises, so the
 *     status/angle path in this port is unaffected. See solve_lcmf.c.
 *   - _tx2_horizontal_proxy and its helpers (horizontal_deg/
 *     horizontal_confidence/horizontal_status are a separate, independent
 *     estimator on a different physical axis -- not this stage).
 *   - track_override / the "accepted_low_confidence_recovery" path: no
 *     corpus case exercises it (generate_lcmf_case never passes it).
 *   - the windowed-range-dump branch (Geometry.range_bin_starts non-NULL):
 *     no corpus case has one, same limitation solve_tracking.c documents.
 *   - raw L3-dump parsing (dump.py/shot.py's parse_dump/project_tx_pair/
 *     prepare_shot_dump): not part of lcmf.py itself, and not one of the
 *     four Tasks-5-8 modules. This stage's inputs are the same
 *     already-parsed MTI cube / Geometry / BallTrack shape solve_tracking.c
 *     consumes -- produced by the (untouched) Python reference in tests,
 *     the same convention Task 5 used for its RANSAC draw callback.
 *
 * Struct-in/struct-out, no globals, no malloc. The snapshot cache is large
 * (~1 MB at the worst-case sizes below), so -- like solve_tracking.c's
 * SolveTrackingWorkspace -- it is a caller-owned pointer, never a stack
 * local or a global.
 */
#ifndef L3_SOLVE_LCMF_H
#define L3_SOLVE_LCMF_H

#include <stdint.h>

/* Mirrors solve_tracking.h's sizing rationale exactly: the vertical LCMF
 * capture uses the identical tracking.Geometry shape (2-TX projected pair,
 * n_rx=4, n_samples up to 128) that solve_tracking.c already sizes for. */
#define SOLVE_LCMF_MAX_FRAMES     64U
#define SOLVE_LCMF_MAX_LOOPS      16U
#define SOLVE_LCMF_MAX_BINS       128U
#define SOLVE_LCMF_N_RX           4U   /* fixed: every configured geometry */
#define SOLVE_LCMF_N_ELEMENTS     8U   /* 2 TX * 4 RX virtual array */

#define SOLVE_LCMF_MAX_SNAPSHOTS  (SOLVE_LCMF_MAX_FRAMES * SOLVE_LCMF_MAX_LOOPS)
#define SOLVE_LCMF_MAX_PER_FRAME  4U   /* lcmf.MAX_PER_FRAME */
#define SOLVE_LCMF_MAX_SELECTED   (SOLVE_LCMF_MAX_FRAMES * SOLVE_LCMF_MAX_PER_FRAME)

/* lcmf.py's grid_deg = np.arange(-5, 45+step/2, step). step as small as
 * 0.1 deg would need 501 points; 0.5 (the module default) needs 101. 512
 * gives headroom without a realistic step ever overflowing it (grid_deg
 * this coarse would defeat the estimator's own purpose). */
#define SOLVE_LCMF_MAX_GRID       512U

/* Dictionary column count: 2 for "two8" (DD, GG), 4 for "four4_path_tdm"
 * (DD, DG, GD, GG). */
#define SOLVE_LCMF_MAX_COEFFS     4U

#define SOLVE_LCMF_OK             0U
#define SOLVE_LCMF_ERROR          1U

#define SOLVE_LCMF_STATUS_LEN     40U

/* Capture layout -- uniform range-dump only (Geometry.range_bin_starts is
 * NULL): every golden case in tests/golden/iwr6843/lcmf/ is uniform, the
 * same limitation solve_tracking.c documents for its own corpus. */
typedef struct {
    uint32_t nFrames;
    uint32_t nLoops;       /* geo.n_loops */
    uint32_t nBins;        /* geo.n_samples: local bin count of the mti cube */
    uint32_t nRx;          /* must equal SOLVE_LCMF_N_RX */
    uint32_t triggerFrame;
    double   framePeriodS;
    double   loopPeriodS;
    double   rangeResM;
    int32_t  rangeBinStart; /* absolute first bin (uniform across frames) */
    /* geo.frame_time_offsets_s: NULL means slot_order * framePeriodS. */
    const double *frameTimeOffsetsS; /* length nFrames, or NULL */
} SolveLcmfLayout;

/* tracking.BallTrack fields this stage actually reads (bin_at/range_at use
 * only slope/intercept; speed_ms_at is never reached because
 * estimate_lcmf_v1 always supplies phase_velocity_ms -- see solve_lcmf.c). */
typedef struct {
    double   slopeBins;
    double   interceptBins;
    double   tFirstS;
    double   tLastS;
} SolveLcmfTrack;

/* Calibration.apply()/true_range() inputs -- calibration.py:81/85. */
typedef struct {
    double elemCorrRe[SOLVE_LCMF_N_ELEMENTS];
    double elemCorrIm[SOLVE_LCMF_N_ELEMENTS];
    double tiltRad;
    double rangeBiasM;
    double teeRangeM;
    double teeBallHeightM;
    double radarHeightM;
} SolveLcmfCalibration;

typedef struct {
    SolveLcmfLayout      layout;
    /* Flat MTI cube, row-major [frame][tx(2)][loop][rx][bin], length
     * nFrames*2*nLoops*nRx*nBins. index(f,tx,l,rx,b) =
     * (((f*2+tx)*nLoops+l)*nRx+rx)*nBins+b. Caller-owned (this stage never
     * copies it wholesale). */
    const double *mtiRe;
    const double *mtiIm;
    double        noisePower;    /* prepared.noise_power(scope) */

    uint8_t  trackFound;         /* shot.track is not None */
    uint8_t  qualityReject;      /* shot.quality == "reject" */
    SolveLcmfTrack track;

    SolveLcmfCalibration cal;

    double  ballSpeedMph;
    uint8_t txOrderReversed;     /* 0 = "normal", 1 = "reversed" */
    int32_t tdmSign;             /* shot.tdm_sign_used: must be -1 or +1 */
    double  tdmTauS;
    double  gridStepDeg;         /* lcmf.py's grid_step_deg, default 0.5 */
} SolveLcmfInput;

typedef struct {
    char   status[SOLVE_LCMF_STATUS_LEN];
    double angleDeg;             /* NAN unless status == "accepted" */
    double rawAngleDeg;          /* NAN unless status == "accepted" */
    /* Diagnostic per-channel estimates (component_values_deg's channel_*
     * entries), always filled once the channel-estimate stage is reached,
     * even when that channel did not end up selected. NAN if never
     * reached (rejected before the channel loop). */
    double channelTwo8Deg;
    double channelFour4PathTdmDeg;
    uint8_t hasChannelTwo8;
    uint8_t hasChannelFour4PathTdm;
    double componentStdDeg;      /* NAN unless status == "accepted" */
    uint8_t  singleChannel;
    uint32_t nChannelsUsed;      /* 0, 1, or 2 */
    char     channelsUsed[2][32];
    uint32_t nSnapshots;
    uint32_t nFrames;
} SolveLcmfResult;

/* Caller-owned scratch: the per-(frame,loop) snapshot cache is ~1 MB at
 * worst-case sizes -- far too large for a stack local, exactly the
 * SolveTrackingWorkspace precedent in solve_tracking.h. */
typedef struct {
    double   t[SOLVE_LCMF_MAX_SNAPSHOTS];
    uint16_t frame[SOLVE_LCMF_MAX_SNAPSHOTS];
    uint16_t loopIdx[SOLVE_LCMF_MAX_SNAPSHOTS];
    double   r[SOLVE_LCMF_MAX_SNAPSHOTS];
    double   snr[SOLVE_LCMF_MAX_SNAPSHOTS];
    double   vr[SOLVE_LCMF_MAX_SNAPSHOTS];
    double   vecRe[SOLVE_LCMF_MAX_SNAPSHOTS][SOLVE_LCMF_N_ELEMENTS];
    double   vecIm[SOLVE_LCMF_MAX_SNAPSHOTS][SOLVE_LCMF_N_ELEMENTS];
    uint32_t nCache;

    uint16_t selected[SOLVE_LCMF_MAX_SELECTED];
    uint32_t nSelected;
} SolveLcmfWorkspace;

/* Runs the scoped estimate_lcmf_v1() vertical launch-angle decision (see
 * the header banner for exactly what that covers). Returns SOLVE_LCMF_OK
 * whenever the input sizes are within the fixed limits above -- *result's
 * status string then carries the accept/reject decision, matching
 * lcmf.py's own status strings byte-for-byte for the covered paths.
 * Returns SOLVE_LCMF_ERROR only when layout sizes exceed the fixed limits;
 * *result is zeroed in that case (no partial write). */
uint32_t solve_lcmf_estimate(const SolveLcmfInput *in,
                              SolveLcmfWorkspace *ws,
                              SolveLcmfResult *result);

#endif /* L3_SOLVE_LCMF_H */
