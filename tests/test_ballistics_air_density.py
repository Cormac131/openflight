"""Tests for density-aware carry in the ballistic model."""

import pytest

from openflight.air_density import AirConditions
from openflight.ballistics import (
    AIR_DENSITY_STD,
    CLUB_TYPICAL_LAUNCH_ANGLE_DEG,
    LaunchConditions,
    density_carry_ratio,
    simulate,
)
from openflight.launch_monitor import ClubType

# Representative tour-average launches, used to check the ratio approximation
# against a directly simulated carry.
PROFILES = {
    ClubType.DRIVER: (167.0, 12.5, 2600.0),
    ClubType.WOOD_3: (158.0, 11.0, 3700.0),
    ClubType.IRON_5: (133.0, 15.0, 5000.0),
    ClubType.IRON_7: (120.0, 18.0, 7000.0),
    ClubType.IRON_9: (109.0, 21.0, 8500.0),
    ClubType.PW: (102.0, 24.0, 9200.0),
    ClubType.SW: (75.0, 28.0, 9500.0),
}

# Sea level in a deep low, through Denver, to a freezing day at sea level.
REALISTIC_DENSITIES = [0.95, 1.00, 1.10, 1.185, 1.225, 1.29, 1.30]


class TestSimulateWithAirDensity:
    def test_thinner_air_carries_further(self):
        conditions = LaunchConditions(167.0, 12.5, 0.0, 2600.0, 0.0, "measured")
        sea_level = simulate(conditions, air_density=1.225).carry_yards
        denver = simulate(conditions, air_density=0.991).carry_yards
        assert denver > sea_level
        # The headline number from the sensitivity analysis: ~14 yd at Denver.
        assert denver - sea_level == pytest.approx(14.0, abs=1.5)

    def test_carry_is_monotonic_in_density(self):
        conditions = LaunchConditions(150.0, 14.0, 0.0, 3000.0, 0.0, "measured")
        carries = [
            simulate(conditions, air_density=rho).carry_yards
            for rho in sorted(REALISTIC_DENSITIES)
        ]
        assert carries == sorted(carries, reverse=True)

    def test_default_density_is_isa_sea_level(self):
        conditions = LaunchConditions(160.0, 13.0, 0.0, 2800.0, 0.0, "measured")
        assert simulate(conditions).carry_yards == pytest.approx(
            simulate(conditions, air_density=AIR_DENSITY_STD).carry_yards
        )


class TestClubTypicalLaunchAngles:
    def test_every_club_type_has_an_entry(self):
        # A missing club would silently fall back to UNKNOWN and quietly
        # mis-scale that club's carry.
        assert set(CLUB_TYPICAL_LAUNCH_ANGLE_DEG) == set(ClubType)

    def test_angles_are_physically_plausible(self):
        assert all(5.0 <= angle <= 40.0 for angle in CLUB_TYPICAL_LAUNCH_ANGLE_DEG.values())

    def test_lofted_clubs_launch_higher_than_the_driver(self):
        driver = CLUB_TYPICAL_LAUNCH_ANGLE_DEG[ClubType.DRIVER]
        assert CLUB_TYPICAL_LAUNCH_ANGLE_DEG[ClubType.IRON_7] > driver
        assert CLUB_TYPICAL_LAUNCH_ANGLE_DEG[ClubType.SW] > CLUB_TYPICAL_LAUNCH_ANGLE_DEG[
            ClubType.IRON_7
        ]


class TestDensityCarryRatio:
    def test_identical_densities_short_circuit_to_one(self):
        assert density_carry_ratio(167.0, ClubType.DRIVER, 2600.0, 1.225, 1.225) == 1.0

    def test_near_identical_densities_short_circuit_to_one(self):
        assert (
            density_carry_ratio(167.0, ClubType.DRIVER, 2600.0, 1.225, 1.225 + 1e-12)
            == 1.0
        )

    def test_thinner_air_gives_a_ratio_above_one(self):
        assert density_carry_ratio(167.0, ClubType.DRIVER, 2600.0, 0.991, 1.225) > 1.0

    def test_denser_air_gives_a_ratio_below_one(self):
        assert density_carry_ratio(167.0, ClubType.DRIVER, 2600.0, 1.292, 1.225) < 1.0

    @pytest.mark.parametrize("club", list(PROFILES))
    @pytest.mark.parametrize("rho", REALISTIC_DENSITIES)
    def test_tracks_a_directly_simulated_carry(self, club, rho):
        """
        The ratio stands in for a real simulation on the table-carry path, so it
        has to agree with one. Tolerance is 1.5 yd across the full realistic
        density range — comfortably inside the table estimator's own error.
        """
        ball_speed, launch_angle, spin = PROFILES[club]
        conditions = LaunchConditions(ball_speed, launch_angle, 0.0, spin, 0.0, "measured")
        reference = simulate(conditions, air_density=AIR_DENSITY_STD).carry_yards
        direct = simulate(conditions, air_density=rho).carry_yards

        approximated = reference * density_carry_ratio(
            ball_speed, club, spin, actual_density=rho
        )
        assert approximated == pytest.approx(direct, abs=1.5)

    def test_unknown_club_still_produces_a_sane_ratio(self):
        ratio = density_carry_ratio(150.0, ClubType.UNKNOWN, 4000.0, 0.991, 1.225)
        assert 1.0 < ratio < 1.15

    def test_ratio_is_monotonic_in_density(self):
        ratios = [
            density_carry_ratio(150.0, ClubType.IRON_7, 6500.0, rho, 1.225)
            for rho in sorted(REALISTIC_DENSITIES)
        ]
        assert ratios == sorted(ratios, reverse=True)

    @pytest.mark.parametrize(
        "actual, reference",
        [(0.0, 1.225), (1.225, 0.0), (-1.0, 1.225), (1.225, -1.0)],
    )
    def test_rejects_non_positive_densities(self, actual, reference):
        with pytest.raises(ValueError):
            density_carry_ratio(167.0, ClubType.DRIVER, 2600.0, actual, reference)

    def test_degenerate_launch_returns_unity_rather_than_dividing_by_zero(self):
        assert density_carry_ratio(0.0, ClubType.DRIVER, 2600.0, 0.991, 1.225) == 1.0


class TestAirConditionsDriveCarry:
    def test_denver_config_reproduces_the_expected_carry_gain(self):
        """End-to-end sanity: config → density → simulated carry."""
        conditions = LaunchConditions(167.0, 12.5, 0.0, 2600.0, 0.0, "measured")
        denver = AirConditions.from_elevation(elevation_ft=5280.0, temperature_c=20.0)
        gain = (
            simulate(conditions, air_density=denver.density_kg_m3).carry_yards
            - simulate(conditions, air_density=AIR_DENSITY_STD).carry_yards
        )
        assert gain == pytest.approx(14.0, abs=1.5)

    def test_a_cold_sea_level_day_shortens_carry(self):
        conditions = LaunchConditions(167.0, 12.5, 0.0, 2600.0, 0.0, "measured")
        winter = AirConditions.from_elevation(elevation_ft=0.0, temperature_c=0.0)
        loss = (
            simulate(conditions, air_density=AIR_DENSITY_STD).carry_yards
            - simulate(conditions, air_density=winter.density_kg_m3).carry_yards
        )
        assert loss == pytest.approx(4.3, abs=1.0)

    def test_standard_conditions_leave_carry_unchanged(self):
        conditions = LaunchConditions(167.0, 12.5, 0.0, 2600.0, 0.0, "measured")
        standard = AirConditions.standard()
        assert simulate(
            conditions, air_density=standard.density_kg_m3
        ).carry_yards == pytest.approx(simulate(conditions).carry_yards, abs=0.01)
