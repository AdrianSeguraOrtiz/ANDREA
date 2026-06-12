"""Shared output bundle contract helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class BundleSpec:
    """User-facing bundle contract metadata."""

    id: str
    label: str
    purpose: str
    intended_downstream_commands: tuple[str, ...] = ()
    contents_summary: tuple[str, ...] = ()
    cli_note: str = (
        "CLI/Python users can pass the report JSON paths directly; ZIP bundles "
        "are mainly for GUI handoff."
    )


@dataclass(frozen=True)
class BundleSource:
    """A concrete file included in a resolved bundle."""

    virtual_path: str
    source_path: Path


@dataclass(frozen=True)
class BundleResolution:
    """Resolved files plus availability diagnostics for a bundle."""

    spec: BundleSpec
    root: Path
    sources: tuple[BundleSource, ...]
    missing_required: tuple[str, ...] = ()
    skipped_optional: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return not self.missing_required

    @property
    def source_tuples(self) -> list[tuple[str, Path]]:
        return [(source.virtual_path, source.source_path) for source in self.sources]


def bundle_spec_by_id(specs: Sequence[BundleSpec], bundle_id: str) -> BundleSpec:
    for spec in specs:
        if spec.id == bundle_id:
            return spec
    supported = ", ".join(spec.id for spec in specs)
    raise ValueError(f"Unsupported bundle_id {bundle_id!r}; supported: {supported}")


def supported_bundle_ids(specs: Sequence[BundleSpec]) -> tuple[str, ...]:
    return tuple(spec.id for spec in specs)


def all_files(root: Path) -> tuple[BundleSource, ...]:
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        return ()
    return unique_sources(
        BundleSource(path.relative_to(root).as_posix(), path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def exact_file(root: Path, relative_path: str) -> BundleSource | None:
    path = (root / relative_path).resolve()
    try:
        virtual_path = path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    if not path.is_file():
        return None
    return BundleSource(virtual_path, path)


def glob_files(root: Path, pattern: str) -> tuple[BundleSource, ...]:
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        return ()
    return unique_sources(
        BundleSource(path.relative_to(root).as_posix(), path)
        for path in sorted(root.glob(pattern))
        if path.is_file()
    )


def unique_sources(sources: Iterable[BundleSource]) -> tuple[BundleSource, ...]:
    unique: dict[str, BundleSource] = {}
    for source in sources:
        unique[source.virtual_path] = source
    return tuple(unique[key] for key in sorted(unique))


def append_exact(
    *,
    root: Path,
    relative_path: str,
    sources: list[BundleSource],
    missing_required: list[str],
    skipped_optional: list[str],
    required: bool,
) -> None:
    source = exact_file(root, relative_path)
    if source is None:
        if required:
            missing_required.append(relative_path)
        else:
            skipped_optional.append(relative_path)
        return
    sources.append(source)


def append_glob(
    *,
    root: Path,
    pattern: str,
    sources: list[BundleSource],
    missing_required: list[str],
    skipped_optional: list[str],
    required: bool,
) -> None:
    matched = list(glob_files(root, pattern))
    if not matched:
        if required:
            missing_required.append(pattern)
        else:
            skipped_optional.append(pattern)
        return
    sources.extend(matched)


def exact_files_resolution(
    *,
    spec: BundleSpec,
    root: Path,
    required: Sequence[str] = (),
    optional: Sequence[str] = (),
) -> BundleResolution:
    """Resolve a bundle made of exact relative file paths."""
    sources: list[BundleSource] = []
    missing_required: list[str] = []
    skipped_optional: list[str] = []
    for relative_path in required:
        append_exact(
            root=root,
            relative_path=relative_path,
            sources=sources,
            missing_required=missing_required,
            skipped_optional=skipped_optional,
            required=True,
        )
    for relative_path in optional:
        append_exact(
            root=root,
            relative_path=relative_path,
            sources=sources,
            missing_required=missing_required,
            skipped_optional=skipped_optional,
            required=False,
        )
    return BundleResolution(
        spec=spec,
        root=root,
        sources=unique_sources(sources),
        missing_required=tuple(missing_required),
        skipped_optional=tuple(skipped_optional),
    )
