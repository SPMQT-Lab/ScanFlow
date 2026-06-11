"""Deterministic synthetic STM surfaces for tracking/drift tests.

This is the saved-data half of ROADMAP §4's rule: feature-discovery,
grouping, atom-tracking, and any future drift-correction strategy must be
exercised against these scenes (and pass tests/test_tracking_dataset.py)
before running on the rig. Everything is seeded — identical arrays on
every run, no files needed.

All generators return ``(image_nm, nm_per_px)`` with row 0 = first
scanline (top), matching ``ScanController.live_data()``. Feature
positions are given and asserted in PIXELS (col, row) to match
``FeatureCandidate.cx_px`` / ``cy_px``.
"""

from __future__ import annotations

import numpy as np

DEFAULT_SHAPE = (512, 512)
DEFAULT_SIZE_NM = 120.0


def terrace(
    shape: tuple[int, int] = DEFAULT_SHAPE,
    size_nm: float = DEFAULT_SIZE_NM,
    *,
    tilt_nm: tuple[float, float] = (0.3, 0.15),
    noise_nm: float = 0.004,
    seed: int = 0,
) -> tuple[np.ndarray, float]:
    """Flat tilted terrace with white noise — the empty background."""
    ny, nx = shape
    rng = np.random.default_rng(seed)
    rows, cols = np.mgrid[:ny, :nx]
    img = (tilt_nm[0] * cols / nx + tilt_nm[1] * rows / ny
           + noise_nm * rng.standard_normal(shape))
    return img.astype(float), size_nm / nx


def add_molecule(
    img: np.ndarray,
    nm_per_px: float,
    col_px: float,
    row_px: float,
    *,
    height_nm: float = 0.15,
    radius_nm: float = 0.8,
) -> None:
    """Add one gaussian adsorbate in place at (col, row) pixels."""
    ny, nx = img.shape
    sigma_px = max(radius_nm / nm_per_px / 2.0, 1.0)
    rows, cols = np.mgrid[:ny, :nx]
    img += height_nm * np.exp(
        -((cols - col_px) ** 2 + (rows - row_px) ** 2) / (2 * sigma_px ** 2)
    )


def sparse_monomers(
    n: int = 10, *, seed: int = 1, margin_px: int = 60,
) -> tuple[np.ndarray, float, list[tuple[float, float]]]:
    """Well-separated monomers on a terrace. Returns (img, nm/px, positions)."""
    img, nm_per_px = terrace(seed=seed)
    ny, nx = img.shape
    rng = np.random.default_rng(seed + 100)
    positions: list[tuple[float, float]] = []
    while len(positions) < n:
        c = rng.uniform(margin_px, nx - margin_px)
        r = rng.uniform(margin_px, ny - margin_px)
        if all((c - pc) ** 2 + (r - pr) ** 2 > 40 ** 2 for pc, pr in positions):
            positions.append((float(c), float(r)))
    for c, r in positions:
        add_molecule(img, nm_per_px, c, r)
    return img, nm_per_px, positions


def clusters(
    *, seed: int = 2,
) -> tuple[np.ndarray, float, list[tuple[float, float]]]:
    """Three separated clusters (2, 3, 5 molecules). Returns cluster centres."""
    img, nm_per_px = terrace(seed=seed)
    centres = [(140.0, 150.0), (330.0, 240.0), (200.0, 400.0)]
    sizes = [2, 3, 5]
    rng = np.random.default_rng(seed + 100)
    spacing_px = 1.2 / nm_per_px  # ~1.2 nm between cluster members
    for (cc, cr), k in zip(centres, sizes):
        for _ in range(k):
            dc = rng.uniform(-spacing_px, spacing_px)
            dr = rng.uniform(-spacing_px, spacing_px)
            add_molecule(img, nm_per_px, cc + dc, cr + dr)
    return img, nm_per_px, centres


def high_density(
    n: int = 60, *, seed: int = 3,
) -> tuple[np.ndarray, float]:
    img, nm_per_px = terrace(seed=seed)
    ny, nx = img.shape
    rng = np.random.default_rng(seed + 100)
    for _ in range(n):
        add_molecule(img, nm_per_px,
                     rng.uniform(30, nx - 30), rng.uniform(30, ny - 30))
    return img, nm_per_px


def noisy_low_contrast(
    n: int = 8, *, seed: int = 4,
) -> tuple[np.ndarray, float, list[tuple[float, float]]]:
    """Molecules barely above the noise floor (height 3× noise rms)."""
    img, nm_per_px = terrace(noise_nm=0.02, seed=seed)
    ny, nx = img.shape
    rng = np.random.default_rng(seed + 100)
    positions = [(float(rng.uniform(80, nx - 80)), float(rng.uniform(80, ny - 80)))
                 for _ in range(n)]
    for c, r in positions:
        add_molecule(img, nm_per_px, c, r, height_nm=0.06)
    return img, nm_per_px, positions


def step_edge(
    *, seed: int = 5,
) -> tuple[np.ndarray, float, list[tuple[float, float]]]:
    """Monatomic step across the frame, molecules on both terraces."""
    img, nm_per_px = terrace(seed=seed)
    ny = img.shape[0]
    img[ny // 2:, :] += 0.25  # lower half is the upper terrace (+0.25 nm)
    positions = [(150.0, 120.0), (380.0, 160.0),   # lower terrace (top half)
                 (160.0, 400.0), (360.0, 380.0)]   # upper terrace (bottom half)
    for c, r in positions:
        add_molecule(img, nm_per_px, c, r)
    return img, nm_per_px, positions


def bare_lattice(
    *, size_nm: float = 30.0, period_nm: float = 0.4, seed: int = 6,
) -> tuple[np.ndarray, float]:
    """Atomically resolved lattice, no adsorbates (30 nm frame resolves it)."""
    img, nm_per_px = terrace(size_nm=size_nm, noise_nm=0.002, seed=seed)
    ny, nx = img.shape
    rows, cols = np.mgrid[:ny, :nx]
    k = 2 * np.pi * nm_per_px / period_nm
    img += 0.02 * np.cos(k * cols) * np.cos(k * rows)
    return img, nm_per_px


def edge_molecules(
    *, seed: int = 7,
) -> tuple[np.ndarray, float, list[tuple[float, float]], list[tuple[float, float]]]:
    """Molecules ON the frame border plus interior controls.

    Returns (img, nm/px, edge_positions, interior_positions).
    """
    img, nm_per_px = terrace(seed=seed)
    ny, nx = img.shape
    edge = [(4.0, 250.0), (250.0, 4.0), (nx - 4.0, 300.0), (300.0, ny - 4.0)]
    interior = [(200.0, 200.0), (350.0, 350.0)]
    for c, r in edge + interior:
        add_molecule(img, nm_per_px, c, r)
    return img, nm_per_px, edge, interior


def drift_pair(
    drift_px: tuple[float, float] = (8.0, 5.0), *, seed: int = 8,
) -> tuple[np.ndarray, np.ndarray, float, list[tuple[float, float]]]:
    """The same molecule field imaged twice, shifted by ``drift_px``.

    Returns (img_before, img_after, nm/px, before_positions). The after
    image has every molecule at position + drift_px (different noise).
    """
    img_a, nm_per_px, positions = sparse_monomers(n=8, seed=seed, margin_px=80)
    img_b, _ = terrace(seed=seed + 1)
    dc, dr = drift_px
    for c, r in positions:
        add_molecule(img_b, nm_per_px, c + dc, r + dr)
    return img_a, img_b, nm_per_px, positions


def inverted_polarity(
    n: int = 8, *, seed: int = 9,
) -> tuple[np.ndarray, float]:
    """Molecules imaged as DEPRESSIONS (e.g. wrong bias / contrast)."""
    img, nm_per_px = terrace(seed=seed)
    ny, nx = img.shape
    rng = np.random.default_rng(seed + 100)
    for _ in range(n):
        add_molecule(img, nm_per_px,
                     rng.uniform(60, nx - 60), rng.uniform(60, ny - 60),
                     height_nm=-0.15)
    return img, nm_per_px


def partial_scan(
    *, blank_from_row: int = 200, seed: int = 10,
) -> tuple[np.ndarray, float, list[tuple[float, float]]]:
    """Interrupted scan: rows below ``blank_from_row`` are zeros.

    Molecules only exist in the scanned (top) region; returns their
    positions.
    """
    img, nm_per_px, positions = sparse_monomers(n=10, seed=seed, margin_px=60)
    img[blank_from_row:, :] = 0.0
    valid = [(c, r) for c, r in positions if r < blank_from_row - 20]
    return img, nm_per_px, valid
