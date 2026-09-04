"""Installer capability-matrix tests."""

from __future__ import annotations

from custom_components.ventaxia_multihome.capabilities import (
    AIRFLOW_FIELDS,
    BOOST_MINIMUM_VALIDATION_FIELDS,
    COMFORT_MODE_FIELDS,
    DELAY_OVERRUN_FIELDS,
    HUMIDITY_RESPONSE_FIELDS,
    INSTALLER_FIELD_DEFINITIONS,
    MODEL_CAPABILITIES,
    SENSOR_THRESHOLD_FIELDS,
    TEMPERATURE_VALIDATION_FIELDS,
    VALIDATED_INSTALLER_WRITE_PROFILES,
    VALIDATION_CANDIDATE_WRITE_PROFILES,
    CapabilityEvidence,
    installer_validation_candidate_fields,
    installer_writable_fields,
)
from custom_components.ventaxia_multihome.protocol import (
    GLOBAL_SETTING_FIELD_SPECS,
    GlobalSettingField,
)


def test_model_matrix_matches_the_recovered_official_app_enum() -> None:
    """Every recovered model number retains its official-app classification."""

    # Arrange - record the complete enum recovered from Connect 7.2.2.
    expected = {
        1: ("SmvPlusHx", True, False),
        2: ("SmvPlusHxCo2", True, True),
        3: ("VaZonalDemand", False, False),
        4: ("ComairFlx", False, False),
        5: ("Unknown5", False, False),
        6: ("ComairZonalDemand", False, False),
        7: ("Unknown7", False, False),
        8: ("Unknown8", False, False),
        9: ("SmvHx", True, False),
        10: ("SmvHxCo2", True, True),
        11: ("Va125Ec3Wireless", False, False),
    }

    # Act - reduce the production matrix to its recovered classification fields.
    actual = {
        number: (
            capability.name,
            capability.four_speed_airflow,
            capability.internal_co2,
        )
        for number, capability in MODEL_CAPABILITIES.items()
    }

    # Assert - unknown/reserved enum names remain explicit and unsupported.
    assert actual == expected


def test_every_packet_136_field_has_one_documented_definition() -> None:
    """The codec and safety matrix cannot drift to different field sets."""

    # Arrange - collect each source of packet-136 field knowledge.
    enum_fields = set(GlobalSettingField)
    codec_fields = set(GLOBAL_SETTING_FIELD_SPECS)
    documented_fields = set(INSTALLER_FIELD_DEFINITIONS)

    # Act - inspect the definitions and all physically validated profiles.
    profile_fields = set().union(
        *(profile.fields for profile in VALIDATED_INSTALLER_WRITE_PROFILES)
    )
    candidate_fields = set().union(
        *(profile.fields for profile in VALIDATION_CANDIDATE_WRITE_PROFILES)
    )

    # Assert - every writable field is documented and all 33 maps are complete.
    assert enum_fields == codec_fields == documented_fields
    assert profile_fields <= documented_fields
    assert candidate_fields <= documented_fields
    assert len(documented_fields) == 33


def test_field_matrix_separates_wire_bounds_from_physical_write_evidence() -> None:
    """Static decoders remain read-only unless physical evidence exists."""

    # Arrange - select one validated field and representative inferred fields.
    low = INSTALLER_FIELD_DEFINITIONS[GlobalSettingField.SPEED_LOW]
    humidity = INSTALLER_FIELD_DEFINITIONS[GlobalSettingField.HUMIDITY_THRESHOLD]
    co2 = INSTALLER_FIELD_DEFINITIONS[GlobalSettingField.CO2_BOOST_THRESHOLD]

    # Act - read the exact code-level metadata used for future controls.
    observed = {
        low.field: (low.record_offset, low.unit, low.minimum, low.maximum, low.step),
        humidity.field: (
            humidity.record_offset,
            humidity.unit,
            humidity.minimum,
            humidity.maximum,
            humidity.step,
        ),
        co2.field: (co2.record_offset, co2.unit, co2.minimum, co2.maximum, co2.step),
    }

    # Assert - ranges and encodings are exact without overstating validation.
    assert observed == {
        GlobalSettingField.SPEED_LOW: (0, "percent", 1, 97, 1),
        GlobalSettingField.HUMIDITY_THRESHOLD: (5, "percent_rh", 0, 100, 1),
        GlobalSettingField.CO2_BOOST_THRESHOLD: (22, "ppm", 0, 2000, 10),
    }
    assert low.evidence is CapabilityEvidence.PHYSICAL_VALIDATION
    assert humidity.evidence is CapabilityEvidence.PHYSICAL_VALIDATION
    assert co2.evidence is CapabilityEvidence.PHYSICAL_VALIDATION


def test_temperature_metadata_matches_the_recovered_multihome_screen() -> None:
    """Temperature diagnostics use only limits and actions shown by the MEV app."""

    # Arrange - select the four fields written by the recovered temperature flow.
    low_action = INSTALLER_FIELD_DEFINITIONS[GlobalSettingField.LOW_THRESHOLD_ACTION]
    high_action = INSTALLER_FIELD_DEFINITIONS[GlobalSettingField.HIGH_THRESHOLD_ACTION]
    low_threshold = INSTALLER_FIELD_DEFINITIONS[
        GlobalSettingField.LOW_TEMPERATURE_THRESHOLD
    ]
    high_threshold = INSTALLER_FIELD_DEFINITIONS[
        GlobalSettingField.HIGH_TEMPERATURE_THRESHOLD
    ]

    # Act - collect the semantic metadata exposed to diagnostics and entities.
    action_metadata = tuple(
        (definition.unit, definition.known_values)
        for definition in (low_action, high_action)
    )
    threshold_metadata = tuple(
        (definition.unit, definition.minimum, definition.maximum)
        for definition in (low_threshold, high_threshold)
    )

    # Assert - no Normal action or inferred low/high ordering is introduced.
    expected_actions = ((1, "low"), (3, "boost"), (4, "purge"))
    assert action_metadata == (
        ("action_code", expected_actions),
        ("action_code", expected_actions),
    )
    assert threshold_metadata == (("celsius", 0, 30), ("celsius", 15, 40))
    assert "no cross-threshold ordering check" in low_threshold.dependencies
    assert "no cross-threshold ordering check" in high_threshold.dependencies


def test_installer_write_matrix_requires_an_exact_validated_identity() -> None:
    """A model match alone never enables an installer-setting write."""

    # Arrange - define the one physically tested identity and near misses.
    identities = [
        (10, "2.03.08", "01.00"),
        (10, "2.03.09", "01.00"),
        (10, "2.03.08", "01.01"),
        (10, None, "01.00"),
        (2, "2.03.08", "01.00"),
    ]

    # Act - resolve each identity through the production capability selector.
    resolved = [installer_writable_fields(*identity) for identity in identities]

    # Assert - only the exact identity exposes all nine validated fields.
    assert resolved == [
        (
            AIRFLOW_FIELDS
            | SENSOR_THRESHOLD_FIELDS
            | HUMIDITY_RESPONSE_FIELDS
            | COMFORT_MODE_FIELDS
        ),
        frozenset(),
        frozenset(),
        frozenset(),
        frozenset(),
    ]


def test_delay_overrun_fields_are_exact_identity_validation_candidates() -> None:
    """The paired LS timers remain separate from stable writable fields."""

    # Arrange - include the intended unit plus firmware, hardware, and model misses.
    identities = [
        (10, "2.03.08", "01.00"),
        (10, "2.03.09", "01.00"),
        (10, "2.03.08", "01.01"),
        (2, "2.03.08", "01.00"),
    ]

    # Act - resolve the deliberately separate validation-candidate matrix.
    resolved = [
        installer_validation_candidate_fields(*identity) for identity in identities
    ]

    # Assert - only the exact identity exposes the restricted candidate fields.
    assert resolved == [
        (
            DELAY_OVERRUN_FIELDS
            | BOOST_MINIMUM_VALIDATION_FIELDS
            | TEMPERATURE_VALIDATION_FIELDS
        ),
        frozenset(),
        frozenset(),
        frozenset(),
    ]
