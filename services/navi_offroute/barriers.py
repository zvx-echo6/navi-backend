"""
PAD-US barrier and wilderness layers for OFFROUTE.

Provides access to:
1. Barrier raster (Pub_Access = 'XA' - closed/restricted areas)
2. Wilderness raster (Des_Tp = 'WA' - designated wilderness areas)

Runtime readers only. The offline raster-build functions that rasterize the
PAD-US geodatabase via gdal/ogr (recon's build_barriers_raster /
build_wilderness_raster) are NOT part of the service — the rasters are a
read-only input produced out of band (Phase A §3/§15.2).
"""
import os
from pathlib import Path
from typing import Tuple, Optional

import numpy as np

try:
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.enums import Resampling
except ImportError:
    raise ImportError("rasterio is required for barriers layer support")

# Default raster paths (single source of truth); env-overridable via the helpers.
DEFAULT_BARRIERS_PATH = Path("/mnt/nav/worldcover/padus_barriers.tif")
DEFAULT_WILDERNESS_PATH = Path("/mnt/nav/worldcover/wilderness.tif")


def barriers_tif_path() -> Path:
    """Barrier raster path, env-overridable via NAVI_OFFROUTE_BARRIERS_TIF."""
    return Path(os.environ.get("NAVI_OFFROUTE_BARRIERS_TIF", str(DEFAULT_BARRIERS_PATH)))


def wilderness_tif_path() -> Path:
    """Wilderness raster path, env-overridable via NAVI_OFFROUTE_WILDERNESS_TIF."""
    return Path(os.environ.get("NAVI_OFFROUTE_WILDERNESS_TIF", str(DEFAULT_WILDERNESS_PATH)))


class BarrierReader:
    """Reader for PAD-US barrier raster (closed/restricted areas)."""

    def __init__(self, barrier_path: Path = None):
        self.barrier_path = Path(barrier_path) if barrier_path else barriers_tif_path()
        self._dataset = None

    def _open(self):
        """Lazy open the dataset."""
        if self._dataset is None:
            if not self.barrier_path.exists():
                raise FileNotFoundError(f"Barrier raster not found at {self.barrier_path}")
            self._dataset = rasterio.open(self.barrier_path)
        return self._dataset

    def get_barrier_grid(
        self,
        south: float,
        north: float,
        west: float,
        east: float,
        target_shape: Tuple[int, int]
    ) -> np.ndarray:
        """
        Get barrier values for a bounding box, resampled to target shape.

        Args:
            south, north, west, east: Bounding box coordinates (WGS84)
            target_shape: (rows, cols) to resample to (matches elevation grid)

        Returns:
            np.ndarray of uint8 barrier values:
                255 = closed/restricted (impassable when respect_boundaries=True)
                0 = public/accessible
        """
        ds = self._open()
        window = from_bounds(west, south, east, north, ds.transform)
        barriers = ds.read(
            1,
            window=window,
            out_shape=target_shape,
            resampling=Resampling.nearest
        )
        return barriers

    def sample_point(self, lat: float, lon: float) -> int:
        """Sample barrier value at a single point."""
        ds = self._open()
        row, col = ds.index(lon, lat)
        if row < 0 or row >= ds.height or col < 0 or col >= ds.width:
            return 0
        window = rasterio.windows.Window(col, row, 1, 1)
        value = ds.read(1, window=window)
        return int(value[0, 0])

    def close(self):
        """Close the dataset."""
        if self._dataset is not None:
            self._dataset.close()
            self._dataset = None


class WildernessReader:
    """Reader for PAD-US wilderness raster (designated wilderness areas)."""

    def __init__(self, wilderness_path: Path = None):
        self.wilderness_path = Path(wilderness_path) if wilderness_path else wilderness_tif_path()
        self._dataset = None

    def _open(self):
        """Lazy open the dataset."""
        if self._dataset is None:
            if not self.wilderness_path.exists():
                raise FileNotFoundError(f"Wilderness raster not found at {self.wilderness_path}")
            self._dataset = rasterio.open(self.wilderness_path)
        return self._dataset

    def get_wilderness_grid(
        self,
        south: float,
        north: float,
        west: float,
        east: float,
        target_shape: Tuple[int, int]
    ) -> np.ndarray:
        """
        Get wilderness values for a bounding box, resampled to target shape.

        Args:
            south, north, west, east: Bounding box coordinates (WGS84)
            target_shape: (rows, cols) to resample to (matches elevation grid)

        Returns:
            np.ndarray of uint8 wilderness values:
                255 = designated wilderness area
                0 = not wilderness
        """
        ds = self._open()
        window = from_bounds(west, south, east, north, ds.transform)
        wilderness = ds.read(
            1,
            window=window,
            out_shape=target_shape,
            resampling=Resampling.nearest
        )
        return wilderness

    def sample_point(self, lat: float, lon: float) -> int:
        """Sample wilderness value at a single point."""
        ds = self._open()
        row, col = ds.index(lon, lat)
        if row < 0 or row >= ds.height or col < 0 or col >= ds.width:
            return 0
        window = rasterio.windows.Window(col, row, 1, 1)
        value = ds.read(1, window=window)
        return int(value[0, 0])

    def close(self):
        """Close the dataset."""
        if self._dataset is not None:
            self._dataset.close()
            self._dataset = None

