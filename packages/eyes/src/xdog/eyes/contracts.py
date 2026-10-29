"""Finite observation contracts, independent of transport implementations.

Composition checks data semantics, not source/detector product-name pairs.
Sources may expose more than one view of the same acquisition.
"""

from __future__ import annotations

from dataclasses import dataclass

from xdog.eyes.models import (
    AvailabilityDetectorDef,
    DetectorDef,
    DetectorDefinition,
    EventSourceDef,
    RecordDetectorDef,
    SchedulerDetectorDef,
    SchedulerSourceDef,
    SourceDef,
    SourceDefinition,
)


@dataclass(frozen=True)
class ObservationContract:
    records: bool = True
    files: bool = False
    occurrences: bool = False
    health: bool = False
    complete: bool = False
    max_records: int = 256


def source_contract(source: SourceDefinition) -> ObservationContract:
    if isinstance(source, SourceDef):
        return ObservationContract(files=True, health=True, complete=True, max_records=source.max_files)
    if isinstance(source, SchedulerSourceDef):
        return ObservationContract(occurrences=True)
    if isinstance(source, EventSourceDef):
        return ObservationContract(max_records=source.max_records)
    # HTTP JSON/status/HTML are complete collections; remote windows are not
    # deletion evidence. Runtime snapshots carry the same conservative semantics.
    return ObservationContract(health=True, complete=source.type == "http", max_records=source.max_records)


def validate_combination(detector: DetectorDefinition, contract: ObservationContract) -> None:
    requirement = (
        "files"
        if isinstance(detector, DetectorDef)
        else "occurrences"
        if isinstance(detector, SchedulerDetectorDef)
        else "health"
        if isinstance(detector, AvailabilityDetectorDef)
        else "records"
    )
    if not getattr(contract, requirement):
        raise ValueError(f"{detector.type} requires the {requirement} observation contract")
    if isinstance(detector, RecordDetectorDef) and detector.removals and not contract.complete:
        raise ValueError("record.change removals requires a complete collection, not a window or stream")
