"""Installer-setting capabilities recovered for MEV/Multihome devices."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .protocol import GLOBAL_SETTING_FIELD_SPECS, GlobalSettingField


class CapabilityEvidence(StrEnum):
    """Strongest evidence supporting one capability definition."""

    STATIC_ANALYSIS = "static_analysis"
    PHYSICAL_VALIDATION = "physical_validation"


class InstallerFieldRisk(StrEnum):
    """Primary consequence of an incorrect installer-setting write."""

    VENTILATION = "ventilation"
    COMFORT = "comfort"
    SENSOR_CONTROL = "sensor_control"
    WIRED_INPUT = "wired_input"


@dataclass(frozen=True, slots=True)
class InstallerFieldDefinition:
    """Documented wire format and safety status for one packet-136 field."""

    field: GlobalSettingField
    encoding: str
    unit: str
    minimum: int
    maximum: int
    step: int
    dependencies: str
    risk: InstallerFieldRisk
    evidence: CapabilityEvidence

    @property
    def attribute(self) -> str:
        """Return the decoded packet-137 attribute used for readback."""

        return GLOBAL_SETTING_FIELD_SPECS[self.field].attribute

    @property
    def record_offset(self) -> int:
        """Return the byte offset in the packet-137 settings record."""

        return GLOBAL_SETTING_FIELD_SPECS[self.field].record_offset


@dataclass(frozen=True, slots=True)
class ModelCapability:
    """Static official-app classification for one model number."""

    model_number: int
    name: str
    four_speed_airflow: bool = False
    internal_co2: bool = False


@dataclass(frozen=True, slots=True)
class InstallerWriteProfile:
    """Physically validated fields for one exact device identity."""

    model_number: int
    firmware: str
    hardware: str
    fields: frozenset[GlobalSettingField]
    evidence: str


def _field(
    field: GlobalSettingField,
    *,
    encoding: str = "uint8",
    unit: str = "raw_code",
    step: int = 1,
    dependencies: str,
    risk: InstallerFieldRisk,
    evidence: CapabilityEvidence = CapabilityEvidence.STATIC_ANALYSIS,
) -> InstallerFieldDefinition:
    """Build a definition while reusing the codec's authoritative bounds."""

    spec = GLOBAL_SETTING_FIELD_SPECS[field]
    return InstallerFieldDefinition(
        field=field,
        encoding=encoding,
        unit=unit,
        minimum=spec.minimum,
        maximum=spec.maximum,
        step=step,
        dependencies=dependencies,
        risk=risk,
        evidence=evidence,
    )


_AIRFLOW_DEPENDENCY: Final = "low < normal < boost < purge"
_INPUT_DEPENDENCY: Final = "installed wiring and model-specific action codes"
_UNVALIDATED_DEPENDENCY: Final = "model-specific interaction is unvalidated"

INSTALLER_FIELD_DEFINITIONS: Final = {
    GlobalSettingField.SPEED_LOW: _field(
        GlobalSettingField.SPEED_LOW,
        unit="percent",
        dependencies=_AIRFLOW_DEPENDENCY,
        risk=InstallerFieldRisk.VENTILATION,
        evidence=CapabilityEvidence.PHYSICAL_VALIDATION,
    ),
    GlobalSettingField.SPEED_MEDIUM: _field(
        GlobalSettingField.SPEED_MEDIUM,
        unit="percent",
        dependencies=_AIRFLOW_DEPENDENCY,
        risk=InstallerFieldRisk.VENTILATION,
        evidence=CapabilityEvidence.PHYSICAL_VALIDATION,
    ),
    GlobalSettingField.SPEED_BOOST: _field(
        GlobalSettingField.SPEED_BOOST,
        unit="percent",
        dependencies=_AIRFLOW_DEPENDENCY,
        risk=InstallerFieldRisk.VENTILATION,
        evidence=CapabilityEvidence.PHYSICAL_VALIDATION,
    ),
    GlobalSettingField.SPEED_PURGE: _field(
        GlobalSettingField.SPEED_PURGE,
        unit="percent",
        dependencies=_AIRFLOW_DEPENDENCY,
        risk=InstallerFieldRisk.VENTILATION,
        evidence=CapabilityEvidence.PHYSICAL_VALIDATION,
    ),
    GlobalSettingField.BOOST_MINIMUM: _field(
        GlobalSettingField.BOOST_MINIMUM,
        unit="percent",
        dependencies=_UNVALIDATED_DEPENDENCY,
        risk=InstallerFieldRisk.VENTILATION,
    ),
    GlobalSettingField.HUMIDITY_THRESHOLD: _field(
        GlobalSettingField.HUMIDITY_THRESHOLD,
        unit="percent_rh",
        dependencies="humidity-capable model; trigger behavior was not exercised",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
        evidence=CapabilityEvidence.PHYSICAL_VALIDATION,
    ),
    GlobalSettingField.COMFORT_ENABLED: _field(
        GlobalSettingField.COMFORT_ENABLED,
        encoding="boolean_uint8",
        unit="boolean",
        dependencies=_UNVALIDATED_DEPENDENCY,
        risk=InstallerFieldRisk.COMFORT,
    ),
    GlobalSettingField.DELAY_ENABLED: _field(
        GlobalSettingField.DELAY_ENABLED,
        encoding="boolean_uint8",
        unit="boolean",
        dependencies="paired with delay_timeout_minutes",
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.OVERRUN_ENABLED: _field(
        GlobalSettingField.OVERRUN_ENABLED,
        encoding="boolean_uint8",
        unit="boolean",
        dependencies="paired with overrun_timeout_minutes",
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.OVERRUN_TIMEOUT_MINUTES: _field(
        GlobalSettingField.OVERRUN_TIMEOUT_MINUTES,
        unit="minutes",
        dependencies="requires overrun_enabled; safe UI range is unvalidated",
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.DELAY_TIMEOUT_MINUTES: _field(
        GlobalSettingField.DELAY_TIMEOUT_MINUTES,
        unit="minutes",
        dependencies="requires delay_enabled; safe UI range is unvalidated",
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.LS1_ACTION: _field(
        GlobalSettingField.LS1_ACTION,
        dependencies=_INPUT_DEPENDENCY,
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.LS2_ACTION: _field(
        GlobalSettingField.LS2_ACTION,
        dependencies=_INPUT_DEPENDENCY,
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.LS3_ACTION: _field(
        GlobalSettingField.LS3_ACTION,
        dependencies=_INPUT_DEPENDENCY,
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.RAPID_RESPONSE_ENABLED: _field(
        GlobalSettingField.RAPID_RESPONSE_ENABLED,
        encoding="boolean_uint8",
        unit="boolean",
        dependencies="humidity response semantics are unvalidated",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
    ),
    GlobalSettingField.AMBIENT_RESPONSE_ENABLED: _field(
        GlobalSettingField.AMBIENT_RESPONSE_ENABLED,
        encoding="boolean_uint8",
        unit="boolean",
        dependencies="humidity response semantics are unvalidated",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
    ),
    GlobalSettingField.LOW_TEMPERATURE_ENABLED: _field(
        GlobalSettingField.LOW_TEMPERATURE_ENABLED,
        encoding="boolean_uint8",
        unit="boolean",
        dependencies="paired with temperature thresholds and actions",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
    ),
    GlobalSettingField.LOW_THRESHOLD_ACTION: _field(
        GlobalSettingField.LOW_THRESHOLD_ACTION,
        dependencies="temperature action codes are unvalidated",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
    ),
    GlobalSettingField.HIGH_THRESHOLD_ACTION: _field(
        GlobalSettingField.HIGH_THRESHOLD_ACTION,
        dependencies="temperature action codes are unvalidated",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
    ),
    GlobalSettingField.LOW_TEMPERATURE_THRESHOLD: _field(
        GlobalSettingField.LOW_TEMPERATURE_THRESHOLD,
        dependencies="temperature unit and safe range are unvalidated",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
    ),
    GlobalSettingField.HIGH_TEMPERATURE_THRESHOLD: _field(
        GlobalSettingField.HIGH_TEMPERATURE_THRESHOLD,
        dependencies="temperature unit and safe range are unvalidated",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
    ),
    GlobalSettingField.CO2_BOOST_THRESHOLD: _field(
        GlobalSettingField.CO2_BOOST_THRESHOLD,
        encoding="uint16_le_div10",
        unit="ppm",
        step=10,
        dependencies="CO2-capable model and boost < purge",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
        evidence=CapabilityEvidence.PHYSICAL_VALIDATION,
    ),
    GlobalSettingField.CO2_PURGE_THRESHOLD: _field(
        GlobalSettingField.CO2_PURGE_THRESHOLD,
        encoding="uint16_le_div10",
        unit="ppm",
        step=10,
        dependencies="CO2-capable model and boost < purge",
        risk=InstallerFieldRisk.SENSOR_CONTROL,
        evidence=CapabilityEvidence.PHYSICAL_VALIDATION,
    ),
    GlobalSettingField.ANALOGUE_INPUT_1_LOW_ACTION: _field(
        GlobalSettingField.ANALOGUE_INPUT_1_LOW_ACTION,
        dependencies=_INPUT_DEPENDENCY,
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.ANALOGUE_INPUT_1_HIGH_ACTION: _field(
        GlobalSettingField.ANALOGUE_INPUT_1_HIGH_ACTION,
        dependencies=_INPUT_DEPENDENCY,
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.ANALOGUE_INPUT_1_LOW_VALUE: _field(
        GlobalSettingField.ANALOGUE_INPUT_1_LOW_VALUE,
        dependencies="analogue-input scaling and paired action are unvalidated",
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.ANALOGUE_INPUT_1_HIGH_VALUE: _field(
        GlobalSettingField.ANALOGUE_INPUT_1_HIGH_VALUE,
        dependencies="analogue-input scaling and paired action are unvalidated",
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.ANALOGUE_INPUT_2_LOW_ACTION: _field(
        GlobalSettingField.ANALOGUE_INPUT_2_LOW_ACTION,
        dependencies=_INPUT_DEPENDENCY,
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.ANALOGUE_INPUT_2_HIGH_ACTION: _field(
        GlobalSettingField.ANALOGUE_INPUT_2_HIGH_ACTION,
        dependencies=_INPUT_DEPENDENCY,
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.ANALOGUE_INPUT_2_LOW_VALUE: _field(
        GlobalSettingField.ANALOGUE_INPUT_2_LOW_VALUE,
        dependencies="analogue-input scaling and paired action are unvalidated",
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.ANALOGUE_INPUT_2_HIGH_VALUE: _field(
        GlobalSettingField.ANALOGUE_INPUT_2_HIGH_VALUE,
        dependencies="analogue-input scaling and paired action are unvalidated",
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.DIGITAL_INPUT_1_ACTION: _field(
        GlobalSettingField.DIGITAL_INPUT_1_ACTION,
        dependencies=_INPUT_DEPENDENCY,
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
    GlobalSettingField.DIGITAL_INPUT_2_ACTION: _field(
        GlobalSettingField.DIGITAL_INPUT_2_ACTION,
        dependencies=_INPUT_DEPENDENCY,
        risk=InstallerFieldRisk.WIRED_INPUT,
    ),
}

MODEL_CAPABILITIES: Final = {
    1: ModelCapability(1, "SmvPlusHx", four_speed_airflow=True),
    2: ModelCapability(
        2,
        "SmvPlusHxCo2",
        four_speed_airflow=True,
        internal_co2=True,
    ),
    3: ModelCapability(3, "VaZonalDemand"),
    4: ModelCapability(4, "ComairFlx"),
    5: ModelCapability(5, "Unknown5"),
    6: ModelCapability(6, "ComairZonalDemand"),
    7: ModelCapability(7, "Unknown7"),
    8: ModelCapability(8, "Unknown8"),
    9: ModelCapability(9, "SmvHx", four_speed_airflow=True),
    10: ModelCapability(
        10,
        "SmvHxCo2",
        four_speed_airflow=True,
        internal_co2=True,
    ),
    11: ModelCapability(11, "Va125Ec3Wireless"),
}

AIRFLOW_FIELDS: Final = frozenset(
    {
        GlobalSettingField.SPEED_LOW,
        GlobalSettingField.SPEED_MEDIUM,
        GlobalSettingField.SPEED_BOOST,
        GlobalSettingField.SPEED_PURGE,
    }
)

SENSOR_THRESHOLD_FIELDS: Final = frozenset(
    {
        GlobalSettingField.HUMIDITY_THRESHOLD,
        GlobalSettingField.CO2_BOOST_THRESHOLD,
        GlobalSettingField.CO2_PURGE_THRESHOLD,
    }
)

HUMIDITY_RESPONSE_FIELDS: Final = frozenset(
    {
        GlobalSettingField.RAPID_RESPONSE_ENABLED,
        GlobalSettingField.AMBIENT_RESPONSE_ENABLED,
    }
)

VALIDATED_INSTALLER_WRITE_PROFILES: Final = (
    InstallerWriteProfile(
        model_number=10,
        firmware="2.03.08",
        hardware="01.00",
        fields=AIRFLOW_FIELDS | SENSOR_THRESHOLD_FIELDS | HUMIDITY_RESPONSE_FIELDS,
        evidence=(
            "fragmented packet-136 writes with exact packet-137 readback; "
            "airflow 6/8/37/50 -> 7/9/38/51 -> 6/8/37/50; "
            "thresholds 81/1500/1750 -> 82/1550/1800 -> 81/1500/1750; "
            "rapid and ambient response independently changed and restored"
        ),
    ),
)

VALIDATION_CANDIDATE_WRITE_PROFILES: Final = ()


def model_capability(model_number: int | None) -> ModelCapability | None:
    """Return the official-app model classification when known."""

    if model_number is None:
        return None
    return MODEL_CAPABILITIES.get(model_number)


def installer_writable_fields(
    model_number: int | None,
    firmware: str | None,
    hardware: str | None,
) -> frozenset[GlobalSettingField]:
    """Return fields validated for this exact model/firmware/hardware identity."""

    if model_number is None or firmware is None or hardware is None:
        return frozenset()
    for profile in VALIDATED_INSTALLER_WRITE_PROFILES:
        if (
            profile.model_number == model_number
            and profile.firmware == firmware
            and profile.hardware == hardware
        ):
            return profile.fields
    return frozenset()


def installer_validation_candidate_fields(
    model_number: int | None,
    firmware: str | None,
    hardware: str | None,
) -> frozenset[GlobalSettingField]:
    """Return guarded prerelease fields awaiting physical validation."""

    if model_number is None or firmware is None or hardware is None:
        return frozenset()
    for profile in VALIDATION_CANDIDATE_WRITE_PROFILES:
        if (
            profile.model_number == model_number
            and profile.firmware == firmware
            and profile.hardware == hardware
        ):
            return profile.fields
    return frozenset()


def installer_configurable_fields(
    model_number: int | None,
    firmware: str | None,
    hardware: str | None,
) -> frozenset[GlobalSettingField]:
    """Return stable and guarded-prerelease fields for one exact identity."""

    return installer_writable_fields(
        model_number, firmware, hardware
    ) | installer_validation_candidate_fields(model_number, firmware, hardware)


def supports_installer_field_write(
    model_number: int | None,
    firmware: str | None,
    hardware: str | None,
    field: GlobalSettingField,
) -> bool:
    """Return whether one field is physically validated for this identity."""

    return field in installer_writable_fields(model_number, firmware, hardware)
